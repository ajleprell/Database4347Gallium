import csv
import sqlite3

DB_FILE = "library.db"

conn = sqlite3.connect(DB_FILE)
conn.execute("PRAGMA foreign_keys = ON;")
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


with open("books.csv", mode="r", encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)
    books = [(row["ISBN10"].strip(), row["Title"].strip()) for row in reader]

cursor.executemany("INSERT INTO BOOK (Isbn, Title) VALUES (?, ?)", books)


with open("authors.csv", mode="r", encoding="utf-8-sig") as f:
    reader = csv.reader(f)
    next(reader)  
    authors = [(int(row[0]), row[1].strip()) for row in reader if row]

cursor.executemany("INSERT INTO AUTHORS (Author_id, Name) VALUES (?, ?)", authors)


with open("book_authors.csv", mode="r", encoding="utf-8-sig") as f:
    reader = csv.reader(f)
    next(reader)  
    book_authors = {
        (int(row[1]), row[0].strip())
        for row in reader
        if len(row) >= 2 and row[1].strip() != ""
    }

cursor.executemany(
    "INSERT INTO BOOK_AUTHORS (Author_id, Isbn) VALUES (?, ?)",
    book_authors
)


with open("borrowers.csv", mode="r", encoding="utf-8-sig") as f:
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

# Functional Requirement 2: Book Search and Availability
def search(search_string):
    pattern = f"%{search_string}%"
    result = cursor.execute("""
        SELECT BOOK.Isbn, BOOK.Title, GROUP_CONCAT(AUTHORS.Name, ', ')
        FROM BOOK, BOOK_AUTHORS, AUTHORS
        WHERE
            (BOOK.Isbn=BOOK_AUTHORS.Isbn AND BOOK_AUTHORS.Author_id=AUTHORS.Author_id)
            AND
            (BOOK.Isbn LIKE ? OR BOOK.Title LIKE ? OR AUTHORS.Name LIKE ?)
        GROUP BY BOOK.Isbn
    """, (pattern,pattern,pattern))
    result = list(result)
    no_maxlen = max(2, len(str(len(result))))
    title_maxlen = 5
    authors_maxlen = 7
    for _, title, authors in result:
        if len(title) > title_maxlen:
            title_maxlen = len(title)
        if len(authors) > authors_maxlen:
            authors_maxlen = len(authors)
    print(f"{'NO':<{no_maxlen}}  {'ISBN':<13}  {'TITLE':<{title_maxlen}}  {'AUTHORS':<{authors_maxlen}}")
    for i, (isbn, title, authors) in enumerate(result, start=1):
        print(f"{i:0>{no_maxlen}}  {isbn:<13}  {title:<{title_maxlen}}  {authors:<{authors_maxlen}}")
       
cursor.close()
conn.close()
