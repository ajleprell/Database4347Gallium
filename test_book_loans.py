import unittest
import sqlite3
import os
import tempfile
import shutil
from datetime import date, timedelta
from decimal import Decimal

from book_loans import (
    checkout,
    search_loans,
    checkin_loans,
    get_book_availability,
    has_unpaid_fines,
    get_active_loan_count,
)
from fines import refresh_fines, pay_fines, view_fines
from main import search


class TestBookLoans(unittest.TestCase):
    def setUp(self):
        """Set up a test database with isolated schema and seed data."""
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_library.db")
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute("PRAGMA foreign_keys = ON;")
        cursor = self.conn.cursor()

        cursor.executescript("""
        CREATE TABLE BOOK (
            Isbn TEXT PRIMARY KEY,
            Title TEXT NOT NULL
        );

        CREATE TABLE AUTHORS (
            Author_id INTEGER PRIMARY KEY,
            Name TEXT NOT NULL
        );

        CREATE TABLE BOOK_AUTHORS (
            Author_id INTEGER NOT NULL,
            Isbn TEXT NOT NULL,
            PRIMARY KEY (Author_id, Isbn),
            FOREIGN KEY (Author_id) REFERENCES AUTHORS(Author_id),
            FOREIGN KEY (Isbn) REFERENCES BOOK(Isbn)
        );

        CREATE TABLE BORROWER (
            Card_id TEXT PRIMARY KEY,
            Ssn TEXT NOT NULL UNIQUE,
            Bname TEXT NOT NULL,
            Address TEXT,
            Phone TEXT,
            Email TEXT
        );

        CREATE TABLE BOOK_LOANS (
            Loan_id INTEGER PRIMARY KEY AUTOINCREMENT,
            Isbn TEXT NOT NULL,
            Card_id TEXT NOT NULL,
            Date_out TEXT NOT NULL,
            Due_date TEXT NOT NULL,
            Date_in TEXT,
            FOREIGN KEY (Isbn) REFERENCES BOOK(Isbn),
            FOREIGN KEY (Card_id) REFERENCES BORROWER(Card_id)
        );

        CREATE TABLE FINES (
            Loan_id INTEGER PRIMARY KEY,
            Fine_amt REAL NOT NULL DEFAULT 0.0,
            Paid INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (Loan_id) REFERENCES BOOK_LOANS(Loan_id)
        );
        """)

        # Seed test books
        books = [
            ("0195153445", "Classical Mythology"),
            ("0002005018", "Clara Callan: A Novel"),
            ("0060973129", "Decision In Normandy"),
            ("0374157065", "Fireside"),
            ("0393045218", "The Mummies of Urumchi"),
        ]
        cursor.executemany("INSERT INTO BOOK VALUES (?, ?)", books)

        # Seed authors
        authors = [(1, "Mark Morford"), (2, "Richard Wright")]
        cursor.executemany("INSERT INTO AUTHORS VALUES (?, ?)", authors)
        cursor.execute("INSERT INTO BOOK_AUTHORS VALUES (1, '0195153445')")
        cursor.execute("INSERT INTO BOOK_AUTHORS VALUES (2, '0002005018')")

        # Seed borrowers
        borrowers = [
            ("ID000001", "111-11-1111", "Alice Smith", "123 Main St", "555-0001", "alice@test.com"),
            ("ID000002", "222-22-2222", "Bob Jones", "456 Oak Ave", "555-0002", "bob@test.com"),
            ("ID000003", "333-33-3333", "Charlie Brown", "789 Pine Rd", "555-0003", "charlie@test.com"),
        ]
        cursor.executemany("INSERT INTO BORROWER VALUES (?, ?, ?, ?, ?, ?)", borrowers)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.test_dir)

    def test_successful_checkout(self):
        """Checkout should succeed and set due date to exactly 14 calendar days from checkout."""
        out_date = date(2026, 11, 1)
        expected_due = date(2026, 11, 15)

        ok, msg, info = checkout("0195153445", "ID000001", date_out=out_date, conn=self.conn)
        self.assertTrue(ok)
        self.assertIn("Success", msg)
        self.assertEqual(info["due_date"], expected_due.isoformat())
        self.assertEqual(info["isbn"], "0195153445")
        self.assertEqual(info["card_id"], "ID000001")

        # Verify in database
        cursor = self.conn.cursor()
        cursor.execute("SELECT Date_out, Due_date, Date_in FROM BOOK_LOANS WHERE Loan_id = ?", (info["loan_id"],))
        row = cursor.fetchone()
        self.assertEqual(row[0], "2026-11-01")
        self.assertEqual(row[1], "2026-11-15")
        self.assertIsNone(row[2])

    def test_checkout_invalid_borrower(self):
        """Checkout should fail if borrower does not exist."""
        ok, msg, _ = checkout("0195153445", "ID999999", conn=self.conn)
        self.assertFalse(ok)
        self.assertIn("Borrower with Card ID 'ID999999' does not exist", msg)

    def test_checkout_invalid_book(self):
        """Checkout should fail if book ISBN does not exist."""
        ok, msg, _ = checkout("9999999999", "ID000001", conn=self.conn)
        self.assertFalse(ok)
        self.assertIn("Book with ISBN '9999999999' does not exist", msg)

    def test_checkout_already_checked_out_book(self):
        """Checkout should fail if the book is already checked out by another or same borrower."""
        ok1, _, _ = checkout("0195153445", "ID000001", conn=self.conn)
        self.assertTrue(ok1)

        # Another borrower tries checking out same book
        ok2, msg2, _ = checkout("0195153445", "ID000002", conn=self.conn)
        self.assertFalse(ok2)
        self.assertIn("is currently checked out", msg2)

        # Same borrower tries checking out same book again
        ok3, msg3, _ = checkout("0195153445", "ID000001", conn=self.conn)
        self.assertFalse(ok3)
        self.assertIn("is currently checked out", msg3)

    def test_max_three_active_loans_limit(self):
        """Each borrower is permitted a maximum of 3 active loans. 4th checkout should fail."""
        ok1, _, _ = checkout("0195153445", "ID000001", conn=self.conn)
        ok2, _, _ = checkout("0002005018", "ID000001", conn=self.conn)
        ok3, _, _ = checkout("0060973129", "ID000001", conn=self.conn)
        self.assertTrue(ok1 and ok2 and ok3)

        self.assertEqual(get_active_loan_count("ID000001", self.conn), 3)

        # Attempt 4th checkout
        ok4, msg4, _ = checkout("0374157065", "ID000001", conn=self.conn)
        self.assertFalse(ok4)
        self.assertIn("already has 3 active book loans", msg4)
        self.assertIn("Maximum allowed is 3", msg4)

    def test_checkout_blocked_by_unpaid_fine(self):
        """If a borrower has an unpaid fine, do not permit them to checkout a book."""
        # Check out and return late to create an unpaid fine
        out_dt = date(2026, 10, 1)
        in_dt = date(2026, 10, 20)  # Due was Oct 15 -> 5 days late -> $1.25 fine
        ok, _, info = checkout("0195153445", "ID000001", date_out=out_dt, conn=self.conn)
        self.assertTrue(ok)

        lid = info["loan_id"]
        ok_in, _, _ = checkin_loans([lid], date_in=in_dt, conn=self.conn)
        self.assertTrue(ok_in)

        # Verify borrower now has unpaid fine
        has_fine, amt, _ = has_unpaid_fines("ID000001", self.conn)
        self.assertTrue(has_fine)
        self.assertEqual(amt, Decimal("1.25"))

        # Attempt new checkout
        ok_next, msg_next, _ = checkout("0002005018", "ID000001", conn=self.conn)
        self.assertFalse(ok_next)
        self.assertIn("Borrower has unpaid fines: $1.25", msg_next)

    def test_search_loans(self):
        """Test searching loans by ISBN, card_id, and borrower name substring."""
        checkout("0195153445", "ID000001", conn=self.conn)
        checkout("0002005018", "ID000002", conn=self.conn)

        # Search by ISBN
        by_isbn = search_loans(isbn="0195153445", conn=self.conn)
        self.assertEqual(len(by_isbn), 1)
        self.assertEqual(by_isbn[0]["card_id"], "ID000001")

        # Search by card_id
        by_card = search_loans(card_id="ID000002", conn=self.conn)
        self.assertEqual(len(by_card), 1)
        self.assertEqual(by_card[0]["isbn"], "0002005018")

        # Search by name substring
        by_name = search_loans(borrower_name="Alice", conn=self.conn)
        self.assertEqual(len(by_name), 1)
        self.assertEqual(by_name[0]["borrower_name"], "Alice Smith")

        # Unified query
        by_query = search_loans(query="Smith", conn=self.conn)
        self.assertEqual(len(by_query), 1)

    def test_checkin_single_and_multiple_books(self):
        """Test checking in 1, 2, or 3 books at once."""
        ok1, _, info1 = checkout("0195153445", "ID000001", conn=self.conn)
        ok2, _, info2 = checkout("0002005018", "ID000001", conn=self.conn)
        ok3, _, info3 = checkout("0060973129", "ID000001", conn=self.conn)
        self.assertTrue(ok1 and ok2 and ok3)

        # Check in 2 books at once
        lids = [info1["loan_id"], info2["loan_id"]]
        ok_in, summary, details = checkin_loans(lids, conn=self.conn)
        self.assertTrue(ok_in)
        self.assertIn("Checked in 2 of 2 book loan(s)", summary)
        self.assertEqual(len(details), 2)

        # Remaining active loans for ID000001 should be 1
        active = search_loans(card_id="ID000001", active_only=True, conn=self.conn)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["loan_id"], info3["loan_id"])

        # Check in the 3rd book individually
        ok_in3, _, _ = checkin_loans([info3["loan_id"]], conn=self.conn)
        self.assertTrue(ok_in3)
        self.assertEqual(len(search_loans(card_id="ID000001", active_only=True, conn=self.conn)), 0)

    def test_checkin_more_than_three_fails(self):
        """Selecting more than 3 checkouts to check in should fail according to spec."""
        ok, msg, _ = checkin_loans([1, 2, 3, 4], conn=self.conn)
        self.assertFalse(ok)
        self.assertIn("Cannot check in more than 3 books at once", msg)

    def test_checkin_late_assesses_correct_fines(self):
        """Check-in of a late book should assess $0.25 per day late and create FINES entry."""
        out_dt = date(2026, 11, 1)   # Due date is Nov 15
        in_dt = date(2026, 11, 19)   # 4 days late -> $1.00 fine
        ok, _, info = checkout("0195153445", "ID000001", date_out=out_dt, conn=self.conn)
        self.assertTrue(ok)

        lid = info["loan_id"]
        ok_in, _, details = checkin_loans([lid], date_in=in_dt, conn=self.conn)
        self.assertTrue(ok_in)
        self.assertEqual(details[0]["days_late"], 4)
        self.assertEqual(details[0]["fine_assessed"], "1.00")

        # Verify FINES table
        cursor = self.conn.cursor()
        cursor.execute("SELECT Fine_amt, Paid FROM FINES WHERE Loan_id = ?", (lid,))
        row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(float(row[0]), 1.00)
        self.assertEqual(row[1], 0)

    def test_book_availability_status_in_search(self):
        """Test that get_book_availability and search() reflect IN vs OUT accurately."""
        self.assertEqual(get_book_availability("0195153445", conn=self.conn), "IN")

        # Checkout book
        checkout("0195153445", "ID000001", conn=self.conn)
        self.assertEqual(get_book_availability("0195153445", conn=self.conn), "OUT")

        # Search should show OUT
        res = search("Classical", conn=self.conn)
        self.assertTrue(any(row[0] == "0195153445" and row[3] == "OUT" for row in res))

        # Checkin book
        loans = search_loans(isbn="0195153445", active_only=True, conn=self.conn)
        checkin_loans([loans[0]["loan_id"]], conn=self.conn)
        self.assertEqual(get_book_availability("0195153445", conn=self.conn), "IN")

        # Search should now show IN
        res = search("Classical", conn=self.conn)
        self.assertTrue(any(row[0] == "0195153445" and row[3] == "IN" for row in res))


if __name__ == "__main__":
    unittest.main()
