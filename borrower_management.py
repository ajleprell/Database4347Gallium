import re
import sqlite3

def create_borrower(
        conn: sqlite3.Connection,
        bname: str,
        ssn: str,
        address: str,
        phone: str
) ->  tuple[bool, str]:
    clean_name = bname.strip() if isinstance(bname, str) else ""
    clean_ssn = ssn.strip() if isinstance(ssn, str) else ""
    clean_address = address.strip() if isinstance(address, str) else ""
    clean_phone = phone.strip() if isinstance(phone, str) and phone.strip() else None

    if not clean_name or not clean_ssn or not clean_address:
        return False, "Error: Name, SSN, and address are required and cannot be blank."

    cursor = conn.cursor()
    try:
        conn.execute("BEGIN IMMEDIATE")
        new_card_id = generate_next_card_id(cursor)
        cursor.execute(
            """
            INSERT INTO BORROWER (Card_id, Ssn, Bname, Address, Phone, Email)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (new_card_id, clean_ssn, clean_name, clean_address, clean_phone, clean_email),
        )
        conn.commit()
        return True, f"Created borrower '{clean_name}' with Card ID {new_card_id}."
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        err_text = str(exc).lower()
        if "ssn" in err_text or "unique" in err_text:
            return False, f"Error: A borrower with SSN '{clean_ssn}' already exists."
        return False, f"Error: Failed to create borrower ({exc})."