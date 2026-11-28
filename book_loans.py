import sqlite3
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional, Union, List, Tuple, Dict, Any

from database import connect


def is_borrower_valid(card_id: str, conn: sqlite3.Connection) -> Tuple[bool, Optional[str]]:
    """Check if borrower exists. Returns (exists, borrower_name)."""
    cursor = conn.cursor()
    cursor.execute("SELECT Bname FROM BORROWER WHERE Card_id = ?", (card_id,))
    row = cursor.fetchone()
    if row:
        return True, row[0]
    return False, None


def is_book_valid(isbn: str, conn: sqlite3.Connection) -> Tuple[bool, Optional[str]]:
    """Check if book exists in BOOK table. Returns (exists, title)."""
    cursor = conn.cursor()
    cursor.execute("SELECT Title FROM BOOK WHERE Isbn = ?", (isbn,))
    row = cursor.fetchone()
    if row:
        return True, row[0]
    return False, None


def get_book_availability(isbn: str, conn: Optional[sqlite3.Connection] = None) -> str:
    """
    Returns 'OUT' if the book is currently checked out (Date_in IS NULL in BOOK_LOANS),
    otherwise 'IN'.
    """
    close_at_end = False
    if conn is None:
        conn = connect()
        close_at_end = True

    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 1 FROM BOOK_LOANS
            WHERE Isbn = ? AND Date_in IS NULL
            LIMIT 1
        """, (isbn,))
        checked_out = cursor.fetchone() is not None
        return "OUT" if checked_out else "IN"
    finally:
        if close_at_end:
            conn.close()


def has_unpaid_fines(card_id: str, conn: sqlite3.Connection, as_of_date: Optional[date] = None) -> Tuple[bool, Decimal, str]:
    """
    Check if a borrower has any unpaid fines (in FINES table with Paid=0 and Fine_amt > 0)
    or overdue active loans as of the specified date.
    Returns (has_unpaid, total_fine_amount, detail_message).
    """
    if as_of_date is None:
        as_of_date = date.today()

    cursor = conn.cursor()

    # 1. Check existing unpaid fines recorded in FINES table
    cursor.execute("""
        SELECT COALESCE(SUM(f.Fine_amt), 0.0)
        FROM FINES f
        JOIN BOOK_LOANS bl ON f.Loan_id = bl.Loan_id
        WHERE bl.Card_id = ? AND f.Paid = 0 AND f.Fine_amt > 0
    """, (card_id,))
    unpaid_fines_amt = Decimal(str(cursor.fetchone()[0]))

    # 2. Check active loans that are past due date as of today (unreturned overdue books)
    cursor.execute("""
        SELECT Loan_id, Due_date
        FROM BOOK_LOANS
        WHERE Card_id = ? AND Date_in IS NULL AND Due_date < ?
    """, (card_id, as_of_date.isoformat()))
    overdue_loans = cursor.fetchall()

    overdue_fine_estimate = Decimal("0.00")
    for loan_id, due_date_str in overdue_loans:
        due = date.fromisoformat(due_date_str)
        days_late = (as_of_date - due).days
        if days_late > 0:
            overdue_fine_estimate += Decimal(days_late) * Decimal("0.25")

    total_unpaid = unpaid_fines_amt + overdue_fine_estimate
    if total_unpaid > Decimal("0.00"):
        msg = f"Borrower has unpaid fines: ${total_unpaid:.2f}"
        if overdue_loans:
            msg += f" (including {len(overdue_loans)} overdue unreturned book(s))"
        return True, total_unpaid, msg

    return False, Decimal("0.00"), "No unpaid fines."


def get_active_loan_count(card_id: str, conn: sqlite3.Connection) -> int:
    """Return count of active (unreturned) loans for a borrower."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COUNT(*)
        FROM BOOK_LOANS
        WHERE Card_id = ? AND Date_in IS NULL
    """, (card_id,))
    return cursor.fetchone()[0]


def is_book_checked_out(isbn: str, conn: sqlite3.Connection) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """Check if a specific ISBN is currently checked out."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT Loan_id, Card_id, Date_out, Due_date
        FROM BOOK_LOANS
        WHERE Isbn = ? AND Date_in IS NULL
    """, (isbn,))
    row = cursor.fetchone()
    if row:
        return True, {
            "loan_id": row[0],
            "card_id": row[1],
            "date_out": row[2],
            "due_date": row[3],
        }
    return False, None


# =====================================================================
# FUNCTIONAL REQUIREMENT 3.1: CHECKING OUT BOOKS
# =====================================================================

def checkout(
    isbn: str,
    card_id: str,
    date_out: Optional[Union[str, date]] = None,
    conn: Optional[sqlite3.Connection] = None
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    Check out a book by ISBN and Borrower Card ID.

    Requirements enforced:
    1. Borrower must exist in the system.
    2. Book must exist in the system.
    3. Borrower cannot have any unpaid fines.
    4. Borrower is permitted a maximum of 3 active BOOK_LOANS. If already 3, checkout fails.
    5. If book is already checked out by any borrower, checkout fails.
    6. Due date is automatically populated as exactly 14 calendar days from checkout date.

    Returns:
        (success: bool, message: str, loan_info: Optional[dict])
    """
    close_at_end = False
    if conn is None:
        conn = connect()
        close_at_end = True

    try:
        clean_isbn = isbn.strip() if isinstance(isbn, str) else ""
        clean_card = card_id.strip() if isinstance(card_id, str) else ""

        if not clean_isbn or not clean_card:
            return False, "Error: Both ISBN and Borrower Card ID are required.", None

        # Parse date_out
        if date_out is None:
            dt_out = date.today()
        elif isinstance(date_out, str):
            dt_out = date.fromisoformat(date_out.strip())
        elif isinstance(date_out, date):
            dt_out = date_out
        else:
            return False, "Error: Invalid date format for date_out.", None

        # Automatically calculate due date: exactly 14 calendar days
        dt_due = dt_out + timedelta(days=14)

        # 1. Verify borrower exists
        borrower_exists, bname = is_borrower_valid(clean_card, conn)
        if not borrower_exists:
            return False, f"Error: Borrower with Card ID '{clean_card}' does not exist.", None

        # 2. Verify book exists
        book_exists, title = is_book_valid(clean_isbn, conn)
        if not book_exists:
            return False, f"Error: Book with ISBN '{clean_isbn}' does not exist.", None

        # 3. Check for unpaid fines
        has_fines, fine_amt, fine_msg = has_unpaid_fines(clean_card, conn, as_of_date=dt_out)
        if has_fines:
            return False, f"Error: Cannot checkout book. {fine_msg}", None

        # 4. Check active loan limit (maximum 3 active loans)
        active_loans = get_active_loan_count(clean_card, conn)
        if active_loans >= 3:
            return False, (
                f"Error: Borrower '{bname}' ({clean_card}) already has {active_loans} active book loans. "
                f"Maximum allowed is 3 active loans."
            ), None

        # 5. Check if book is currently checked out
        checked_out, loan_details = is_book_checked_out(clean_isbn, conn)
        if checked_out:
            return False, (
                f"Error: Book '{title}' (ISBN: {clean_isbn}) is currently checked out "
                f"by Borrower {loan_details['card_id']} (Due: {loan_details['due_date']}) and is not available."
            ), None

        # Insert new BOOK_LOANS tuple
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO BOOK_LOANS (Isbn, Card_id, Date_out, Due_date, Date_in)
            VALUES (?, ?, ?, ?, NULL)
        """, (clean_isbn, clean_card, dt_out.isoformat(), dt_due.isoformat()))
        conn.commit()

        loan_id = cursor.lastrowid
        loan_info = {
            "loan_id": loan_id,
            "isbn": clean_isbn,
            "title": title,
            "card_id": clean_card,
            "borrower_name": bname,
            "date_out": dt_out.isoformat(),
            "due_date": dt_due.isoformat(),
        }

        success_msg = (
            f"Success: Loan #{loan_id} created. Book '{title}' (ISBN: {clean_isbn}) "
            f"checked out to {bname} ({clean_card}). Due date: {dt_due.isoformat()}."
        )
        return True, success_msg, loan_info

    finally:
        if close_at_end:
            conn.close()


# =====================================================================
# FUNCTIONAL REQUIREMENT 3.2: CHECKING IN BOOKS
# =====================================================================

def search_loans(
    query: Optional[str] = None,
    isbn: Optional[str] = None,
    card_id: Optional[str] = None,
    borrower_name: Optional[str] = None,
    active_only: bool = True,
    conn: Optional[sqlite3.Connection] = None
) -> List[Dict[str, Any]]:
    """
    Locate BOOK_LOANS tuples by searching on any of:
      - BOOKS.isbn
      - BORROWER.card_no (Card_id)
      - substring of BORROWER name (Bname)

    Can accept a single unified search 'query' (matching across ISBN, Card_id, or Bname)
    or individual criteria (isbn, card_id, borrower_name).

    If active_only=True (default), returns only currently checked out books (Date_in IS NULL).
    """
    close_at_end = False
    if conn is None:
        conn = connect()
        close_at_end = True

    try:
        cursor = conn.cursor()

        sql = """
            SELECT
                bl.Loan_id,
                bl.Isbn,
                b.Title,
                bl.Card_id,
                br.Bname,
                bl.Date_out,
                bl.Due_date,
                bl.Date_in
            FROM BOOK_LOANS bl
            JOIN BOOK b ON bl.Isbn = b.Isbn
            JOIN BORROWER br ON bl.Card_id = br.Card_id
            WHERE 1=1
        """
        params: List[Any] = []

        if active_only:
            sql += " AND bl.Date_in IS NULL"

        if query and query.strip():
            pat = f"%{query.strip()}%"
            sql += " AND (bl.Isbn LIKE ? OR bl.Card_id LIKE ? OR br.Bname LIKE ? OR b.Title LIKE ?)"
            params.extend([pat, pat, pat, pat])
        else:
            if isbn and isbn.strip():
                sql += " AND bl.Isbn LIKE ?"
                params.append(f"%{isbn.strip()}%")
            if card_id and card_id.strip():
                sql += " AND bl.Card_id LIKE ?"
                params.append(f"%{card_id.strip()}%")
            if borrower_name and borrower_name.strip():
                sql += " AND br.Bname LIKE ?"
                params.append(f"%{borrower_name.strip()}%")

        sql += " ORDER BY bl.Loan_id ASC"

        cursor.execute(sql, params)
        rows = cursor.fetchall()

        today = date.today()
        results = []
        for r in rows:
            loan_id, r_isbn, title, r_card, bname, d_out, d_due, d_in = r
            due_dt = date.fromisoformat(d_due)
            ret_dt = date.fromisoformat(d_in) if d_in else today

            days_late = (ret_dt - due_dt).days
            is_late = days_late > 0
            fine_est = round(Decimal(days_late) * Decimal("0.25"), 2) if is_late else Decimal("0.00")

            results.append({
                "loan_id": loan_id,
                "isbn": r_isbn,
                "title": title,
                "card_id": r_card,
                "borrower_name": bname,
                "date_out": d_out,
                "due_date": d_due,
                "date_in": d_in,
                "is_late": is_late,
                "days_late": max(0, days_late),
                "fine_amount": str(fine_est),
            })
        return results
    finally:
        if close_at_end:
            conn.close()


def checkin_loans(
    loan_ids: Union[int, str, List[Union[int, str]]],
    date_in: Optional[Union[str, date]] = None,
    conn: Optional[sqlite3.Connection] = None
) -> Tuple[bool, str, List[Dict[str, Any]]]:
    """
    Check in 1 to 3 selected book loans.

    Parameters:
        loan_ids: A single loan_id or list of 1-3 loan_ids.
        date_in: Return date (defaults to date.today()).
        conn: Optional SQLite connection.

    Actions performed:
        - Updates Date_in in BOOK_LOANS.
        - If returned late, calculates fine at $0.25/day and creates or updates FINES record.
        - Validates selection of 1 to 3 checkouts.

    Returns:
        (success: bool, summary_message: str, details: list[dict])
    """
    close_at_end = False
    if conn is None:
        conn = connect()
        close_at_end = True

    try:
        # Standardize loan_ids into a list of ints
        if isinstance(loan_ids, (int, str)):
            raw_ids = [loan_ids]
        elif isinstance(loan_ids, (list, tuple, set)):
            raw_ids = list(loan_ids)
        else:
            return False, "Error: Invalid loan_ids format.", []

        if len(raw_ids) == 0:
            return False, "Error: No loan IDs selected for check-in.", []

        if len(raw_ids) > 3:
            return False, f"Error: Cannot check in more than 3 books at once (requested {len(raw_ids)}).", []

        clean_loan_ids = []
        for x in raw_ids:
            try:
                clean_loan_ids.append(int(x))
            except (ValueError, TypeError):
                return False, f"Error: Invalid loan ID '{x}'. Must be an integer.", []

        # Parse date_in
        if date_in is None:
            dt_in = date.today()
        elif isinstance(date_in, str):
            dt_in = date.fromisoformat(date_in.strip())
        elif isinstance(date_in, date):
            dt_in = date_in
        else:
            return False, "Error: Invalid date format for date_in.", []

        dt_in_str = dt_in.isoformat()
        cursor = conn.cursor()

        details = []
        all_ok = True

        for lid in clean_loan_ids:
            cursor.execute("""
                SELECT bl.Loan_id, bl.Isbn, b.Title, bl.Card_id, br.Bname, bl.Due_date, bl.Date_in
                FROM BOOK_LOANS bl
                JOIN BOOK b ON bl.Isbn = b.Isbn
                JOIN BORROWER br ON bl.Card_id = br.Card_id
                WHERE bl.Loan_id = ?
            """, (lid,))
            row = cursor.fetchone()

            if not row:
                all_ok = False
                details.append({
                    "loan_id": lid,
                    "success": False,
                    "message": f"Loan ID #{lid} not found."
                })
                continue

            r_lid, isbn, title, card_id, bname, due_date_str, existing_date_in = row

            if existing_date_in is not None:
                all_ok = False
                details.append({
                    "loan_id": lid,
                    "isbn": isbn,
                    "title": title,
                    "card_id": card_id,
                    "success": False,
                    "message": f"Loan ID #{lid} was already checked in on {existing_date_in}."
                })
                continue

            # Update Date_in in BOOK_LOANS
            cursor.execute("""
                UPDATE BOOK_LOANS
                SET Date_in = ?
                WHERE Loan_id = ?
            """, (dt_in_str, lid))

            # Assess fines if returned late
            due_dt = date.fromisoformat(due_date_str)
            days_late = (dt_in - due_dt).days
            fine_assessed = Decimal("0.00")

            if days_late > 0:
                fine_assessed = Decimal(days_late) * Decimal("0.25")
                # Check if FINES entry already exists
                cursor.execute("SELECT Fine_amt, Paid FROM FINES WHERE Loan_id = ?", (lid,))
                existing_fine = cursor.fetchone()

                if existing_fine is None:
                    cursor.execute("""
                        INSERT INTO FINES (Loan_id, Fine_amt, Paid)
                        VALUES (?, ?, 0)
                    """, (lid, str(fine_assessed)))
                elif existing_fine[1] == 0:
                    cursor.execute("""
                        UPDATE FINES
                        SET Fine_amt = ?
                        WHERE Loan_id = ?
                    """, (str(fine_assessed), lid))

            details.append({
                "loan_id": lid,
                "isbn": isbn,
                "title": title,
                "card_id": card_id,
                "borrower_name": bname,
                "date_in": dt_in_str,
                "due_date": due_date_str,
                "days_late": max(0, days_late),
                "fine_assessed": str(fine_assessed),
                "success": True,
                "message": (
                    f"Loan #{lid} checked in successfully. "
                    + (f"LATE by {days_late} day(s). Fine assessed: ${fine_assessed:.2f}."
                       if days_late > 0 else "Returned on time. No fine.")
                )
            })

        conn.commit()

        success_count = sum(1 for d in details if d.get("success"))
        summary = f"Checked in {success_count} of {len(clean_loan_ids)} book loan(s)."
        return all_ok, summary, details

    finally:
        if close_at_end:
            conn.close()


def print_loan_results(loans: List[Dict[str, Any]]):
    """Utility to print loans in an aligned console table."""
    if not loans:
        print("No matching book loans found.")
        return

    print(f"{'#':<4} {'LOAN ID':<8} {'ISBN':<12} {'BORROWER ID':<13} {'BORROWER NAME':<22} {'DUE DATE':<11} {'STATUS':<8} {'FINE':<7} {'TITLE'}")
    print("-" * 105)
    for idx, l in enumerate(loans, start=1):
        status = "IN" if l.get("date_in") else ("LATE" if l.get("is_late") else "OUT")
        fine = f"${l.get('fine_amount', '0.00')}"
        title = l['title']
        if len(title) > 30:
            title = title[:27] + "..."
        bname = l['borrower_name']
        if len(bname) > 20:
            bname = bname[:17] + "..."
        print(f"[{idx}]  {l['loan_id']:<8} {l['isbn']:<12} {l['card_id']:<13} {bname:<22} {l['due_date']:<11} {status:<8} {fine:<7} {title}")


def interactive_menu():
    """Interactive command-line menu for testing Book Loans functionality."""
    print("=" * 60)
    print("  LIBRARY SYSTEM - BOOK LOANS MANAGEMENT")
    print("=" * 60)

    while True:
        print("\nSelect an option:")
        print("1. Check Out a Book (ISBN & Borrower ID)")
        print("2. Search Active Loans (by ISBN, Card ID, or Name)")
        print("3. Check In Books (Select 1-3 loans)")
        print("4. View All Active Loans")
        print("5. Exit")

        choice = input("\nEnter choice [1-5]: ").strip()
        if choice == "1":
            isbn = input("Enter Book ISBN: ").strip()
            card = input("Enter Borrower Card ID (e.g. ID000001): ").strip()
            success, msg, _ = checkout(isbn, card)
            print("\n" + msg)

        elif choice == "2":
            q = input("Enter search query (ISBN, Card ID, or Borrower Name): ").strip()
            loans = search_loans(query=q, active_only=True)
            print(f"\nFound {len(loans)} matching active loan(s):")
            print_loan_results(loans)

        elif choice == "3":
            q = input("Search loans to check in (leave blank to see all active): ").strip()
            loans = search_loans(query=q if q else None, active_only=True)
            if not loans:
                print("No active loans found.")
                continue

            print("\nSelect loans to return:")
            print_loan_results(loans)

            selection = input("\nEnter 1-3 list indices to check in (e.g. '1' or '1, 2, 3'): ").strip()
            if not selection:
                print("Check-in cancelled.")
                continue

            try:
                indices = [int(x.strip()) for x in selection.split(",") if x.strip()]
            except ValueError:
                print("Invalid indices.")
                continue

            if len(indices) < 1 or len(indices) > 3:
                print("You must select between 1 and 3 books to check in.")
                continue

            selected_loan_ids = []
            for idx in indices:
                if 1 <= idx <= len(loans):
                    selected_loan_ids.append(loans[idx - 1]["loan_id"])
                else:
                    print(f"Index [{idx}] is out of range.")
                    selected_loan_ids = []
                    break

            if selected_loan_ids:
                ok, summary, details = checkin_loans(selected_loan_ids)
                print(f"\n{summary}")
                for d in details:
                    print(" - " + d["message"])

        elif choice == "4":
            loans = search_loans(active_only=True)
            print(f"\nAll Active Loans ({len(loans)}):")
            print_loan_results(loans)

        elif choice == "5":
            print("Exiting Book Loans.")
            break
        else:
            print("Invalid selection. Please choose 1-5.")


if __name__ == "__main__":
    interactive_menu()
