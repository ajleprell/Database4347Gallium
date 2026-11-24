
from database import connect
from datetime import date
from decimal import Decimal


# Calculate and update fines for overdue books
def refresh_fines():
    conn = connect()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT Loan_id, Due_date, Date_in
        FROM BOOK_LOANS
    """)

    loans = cursor.fetchall()
    today = date.today()
    added = 0
    updated = 0

    for loan_id, due_date, date_in in loans:

        due = date.fromisoformat(due_date)

        # Use return date if returned, otherwise today's date
        if date_in is not None:
            end_date = date.fromisoformat(date_in)
        else:
            end_date = today

        days_late = (end_date - due).days

        # Skip books that are not overdue
        if days_late <= 0:
            continue

        fine_amt = Decimal(days_late) * Decimal("0.25")

        # Check if a fine already exists
        cursor.execute("""
            SELECT Fine_amt, Paid
            FROM FINES
            WHERE Loan_id = ?
        """, (loan_id,))

        existing = cursor.fetchone()

        # Create a new fine
        if existing is None:
            cursor.execute("""
                INSERT INTO FINES (Loan_id, Fine_amt, Paid)
                VALUES (?, ?, 0)
            """, (loan_id, str(fine_amt)))

            added += 1

        # Update existing fine only if unpaid
        elif existing[1] == 0:
            if Decimal(str(existing[0])) != fine_amt:
                cursor.execute("""
                    UPDATE FINES
                    SET Fine_amt = ?
                    WHERE Loan_id = ?
                """, (str(fine_amt), loan_id))

                updated += 1

    conn.commit()
    conn.close()

    return f"Fines refreshed: {added} added, {updated} updated."


# Display total fines grouped by borrower
def view_fines(unpaid_only=True):
    conn = connect()
    cursor = conn.cursor()

    query = """
        SELECT
            bl.Card_id,
            SUM(f.Fine_amt)
        FROM FINES f
        JOIN BOOK_LOANS bl
            ON f.Loan_id = bl.Loan_id
    """

    # Hide paid fines by default
    if unpaid_only:
        query += " WHERE f.Paid = 0"

    query += """
        GROUP BY bl.Card_id
        ORDER BY bl.Card_id
    """

    cursor.execute(query)
    results = cursor.fetchall()

    conn.close()

    return results


# Pay all outstanding fines for a borrower
def pay_fines(card_id):
    refresh_fines()

    conn = connect()
    cursor = conn.cursor()

    # Find all unpaid fines for the borrower
    cursor.execute("""
        SELECT f.Loan_id, f.Fine_amt, bl.Date_in
        FROM FINES f
        JOIN BOOK_LOANS bl
            ON f.Loan_id = bl.Loan_id
        WHERE bl.Card_id = ?
        AND f.Paid = 0
    """, (card_id,))

    fines = cursor.fetchall()

    if not fines:
        conn.close()
        return "No unpaid fines found."

    # Reject payment if an overdue book is still out
    for loan_id, fine_amt, date_in in fines:
        if date_in is None:
            conn.close()
            return "Payment denied: overdue books must be returned first."

    # Calculate total payment
    total = sum(
        (Decimal(str(fine[1])) for fine in fines),
        Decimal("0.00")
    )

    # Mark all outstanding fines as paid
    cursor.execute("""
        UPDATE FINES
        SET Paid = 1
        WHERE Paid = 0
        AND Loan_id IN (
            SELECT Loan_id
            FROM BOOK_LOANS
            WHERE Card_id = ?
        )
    """, (card_id,))

    conn.commit()
    conn.close()

    return f"Payment successful. Total paid: ${total:.2f}"
