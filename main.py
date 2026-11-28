import csv
import sqlite3
import os
from database import connect

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "library.db")


def init_db(db_file=DB_FILE):
    """Initializes the database schema and populates tables from CSV files."""
    conn = connect(db_file)
    cursor = conn.cursor()

    cursor.executescript("""
    DROP TABLE IF EXISTS FINES;
    DROP TABLE IF EXISTS BOOK_LOANS;
    DROP TABLE IF EXISTS BOOK_AUTHORS;
    DROP TABLE IF EXISTS BORROWER;
    DROP TABLE IF EXISTS AUTHORS;
    DROP TABLE IF EXISTS BOOK;

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

    base_dir = os.path.dirname(os.path.abspath(__file__))

    books_csv = os.path.join(base_dir, "books.csv")
    with open(books_csv, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        books = [(row["ISBN10"].strip(), row["Title"].strip()) for row in reader]
    cursor.executemany("INSERT INTO BOOK (Isbn, Title) VALUES (?, ?)", books)

    authors_csv = os.path.join(base_dir, "authors.csv")
    with open(authors_csv, mode="r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        next(reader)
        authors = [(int(row[0]), row[1].strip()) for row in reader if row]
    cursor.executemany("INSERT INTO AUTHORS (Author_id, Name) VALUES (?, ?)", authors)

    book_authors_csv = os.path.join(base_dir, "book_authors.csv")
    with open(book_authors_csv, mode="r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        next(reader)
        book_authors = {
            (int(row[1]), row[0].strip())
            for row in reader
            if len(row) >= 2 and row[1].strip() != ""
        }
    cursor.executemany("INSERT INTO BOOK_AUTHORS (Author_id, Isbn) VALUES (?, ?)", book_authors)

    borrowers_csv = os.path.join(base_dir, "borrowers.csv")
    with open(borrowers_csv, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        borrowers = [
            (
                row["ID0000id"].strip(),
                row["ssn"].strip(),
                f"{row['first_name'].strip()} {row['last_name'].strip()}",
                None,
                None,
                row["email"].strip(),
            )
            for row in reader
        ]
    cursor.executemany(
        "INSERT INTO BORROWER (Card_id, Ssn, Bname, Address, Phone, Email) VALUES (?, ?, ?, ?, ?, ?)",
        borrowers
    )

    conn.commit()

    for table in ["BOOK", "AUTHORS", "BOOK_AUTHORS", "BORROWER", "BOOK_LOANS", "FINES"]:
        count = cursor.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"{table:<14} -> {count:,} rows")

    cursor.close()
    conn.close()


# Functional Requirement 2: Book Search and Availability
def search(search_string, conn=None):
    """
    Search for a book given any combination of ISBN, Title, and/or Author(s).
    Single text search field, case-insensitive substring matching.
    Displays and returns the four required columns:
    - ISBN
    - Book title
    - Book author(s) (comma-separated list)
    - Book availability (STATUS: 'IN' or 'OUT')
    """
    close_at_end = False
    if conn is None:
        conn = connect()
        close_at_end = True

    try:
        cursor = conn.cursor()
        pattern = f"%{search_string}%"
        result = cursor.execute("""
            SELECT
                BOOK.Isbn,
                BOOK.Title,
                GROUP_CONCAT(AUTHORS.Name, ', ') AS Authors,
                CASE
                    WHEN EXISTS (
                        SELECT 1 FROM BOOK_LOANS
                        WHERE BOOK_LOANS.Isbn = BOOK.Isbn AND BOOK_LOANS.Date_in IS NULL
                    ) THEN 'OUT'
                    ELSE 'IN'
                END AS Status
            FROM BOOK
            JOIN BOOK_AUTHORS ON BOOK.Isbn = BOOK_AUTHORS.Isbn
            JOIN AUTHORS ON BOOK_AUTHORS.Author_id = AUTHORS.Author_id
            WHERE
                (BOOK.Isbn LIKE ? OR BOOK.Title LIKE ? OR AUTHORS.Name LIKE ?)
            GROUP BY BOOK.Isbn
        """, (pattern, pattern, pattern))

        result = list(result)
        no_maxlen = max(2, len(str(len(result)))) if result else 2
        title_maxlen = 5
        authors_maxlen = 7
        for _, title, authors, _ in result:
            if len(title) > title_maxlen:
                title_maxlen = len(title)
            if authors and len(authors) > authors_maxlen:
                authors_maxlen = len(authors)

        print(f"{'NO':<{no_maxlen}}  {'ISBN':<13}  {'TITLE':<{title_maxlen}}  {'AUTHORS':<{authors_maxlen}}  {'STATUS'}")
        for i, (isbn, title, authors, status) in enumerate(result, start=1):
            print(f"{i:0>{no_maxlen}}  {isbn:<13}  {title:<{title_maxlen}}  {authors:<{authors_maxlen}}  {status}")

        return result
    finally:
        if close_at_end:
            conn.close()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--init":
        print("Reinitializing database...")
        init_db()
    elif len(sys.argv) > 1:
        search(" ".join(sys.argv[1:]))
    else:
        conn = connect()
        cursor = conn.cursor()
        for table in ["BOOK", "AUTHORS", "BOOK_AUTHORS", "BORROWER", "BOOK_LOANS", "FINES"]:
            count = cursor.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"{table:<14} -> {count:,} rows")
        conn.close()
