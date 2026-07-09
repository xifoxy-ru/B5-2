# B5-2 Library Book Manager

B5-2 Library Book Manager는 FastAPI, Jinja2, SQLite, SQLAlchemy로 만든 서버 사이드 렌더링 Book CRUD 웹 애플리케이션입니다.

이 프로젝트의 CRUD 대상은 `Book`만입니다. `Author`와 `Category`는 seed/reference table로 사용하며, Book 등록/수정 form의 select box에 표시됩니다.

## Scope

B5-2는 B5-1의 도서관 DB 전체를 구현하지 않습니다. B5-1 주제에서 B5-2에 필요한 최소 범위인 `books`, `authors`, `categories`만 가져왔습니다.

B5-3로 보류한 범위:

- members
- loans
- authentication
- login/logout
- authorization
- borrow/return workflow
- overdue workflow

## Tech Stack

- Python 3.12
- FastAPI
- Uvicorn
- Jinja2
- SQLite
- SQLAlchemy ORM
- python-multipart
- uv

주요 runtime dependency는 `requirements.txt`에 고정되어 있습니다.

## Install

```bash
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

`uv`가 없는 환경에서는 Python 3.12 가상환경을 직접 만든 뒤 `requirements.txt`를 설치해도 됩니다.

## Run

```bash
source .venv/bin/activate
uvicorn app.main:app --reload
```

브라우저에서 다음 주소로 접속합니다.

```text
http://127.0.0.1:8000
```

앱 시작 시 `library.db`가 없으면 SQLite DB 파일이 생성되고, `authors`, `categories` seed data가 없을 때만 삽입됩니다.

## Main URLs

| Method | URL | Description |
| --- | --- | --- |
| `GET` | `/` | Home page |
| `GET` | `/books` | Book list and title search |
| `GET` | `/books/new` | New book form |
| `POST` | `/books` | Create book |
| `GET` | `/books/{book_id}` | Book detail |
| `GET` | `/books/{book_id}/edit` | Edit book form |
| `POST` | `/books/{book_id}/edit` | Update book |
| `POST` | `/books/{book_id}/delete` | Delete book |

Delete는 POST form으로만 처리합니다. GET delete route는 없습니다.

## Project Structure

```text
app/
├── __init__.py
├── main.py
├── database.py
├── models/
│   ├── __init__.py
│   ├── author.py
│   ├── category.py
│   └── book.py
├── repositories/
│   ├── __init__.py
│   ├── author_repository.py
│   ├── category_repository.py
│   └── book_repository.py
├── services/
│   ├── __init__.py
│   └── book_service.py
├── routers/
│   ├── __init__.py
│   └── book_router.py
└── templates/
    ├── base.html
    ├── home.html
    ├── book_list.html
    ├── book_detail.html
    ├── book_form.html
    └── error.html
```

Layer responsibility:

- `routers/`: HTTP boundary, `Request`, `Form`, `TemplateResponse`, `RedirectResponse`
- `services/`: validation and application workflow
- `repositories/`: SQLAlchemy query and persistence only
- `models/`: SQLAlchemy ORM models only
- `templates/`: Jinja2 SSR pages
- `database.py`: engine, `SessionLocal`, `Base`, `get_db`, init/seed functions

## Database Tables

SQLite database URL:

```text
sqlite:///./library.db
```

### authors

| Column | Description |
| --- | --- |
| `author_id` | Primary key |
| `author_name` | Required, unique |
| `nationality` | Optional |

### categories

| Column | Description |
| --- | --- |
| `category_id` | Primary key |
| `category_name` | Required, unique |
| `description` | Optional |

### books

| Column | Description |
| --- | --- |
| `book_id` | Primary key |
| `title` | Required |
| `author_id` | Required FK to `authors.author_id` |
| `category_id` | Required FK to `categories.category_id` |
| `published_year` | Required integer |
| `price` | Required float |
| `stock_quantity` | Required integer, default `1` |
| `isbn` | Required, unique |

Relationships:

- `Author.books`
- `Category.books`
- `Book.author`
- `Book.category`

## Features

- Server-rendered HTML pages with Jinja2
- Book list
- Title search
- Book detail page
- Book create form
- Book edit form
- Book delete via POST form
- Author/category select boxes
- Field-level validation errors
- HTML error pages for common browser errors
- Price display with thousands separators

## Input Validation Policy

Validation is enforced in `app/services/book_service.py`.

### title

- Required
- Leading/trailing whitespace is trimmed
- Consecutive internal whitespace is collapsed to one space
- Blank title is rejected
- Title is not unique

Examples:

```text
"     1     2" -> "1 2"
"  1 2 " -> "1 2"
```

### isbn

- Required
- Leading/trailing whitespace is trimmed
- Internal whitespace is rejected
- Only digits and hyphens are allowed
- Must not start or end with a hyphen
- Must not contain consecutive hyphens
- Hyphens are ignored for length checking
- Digit length must be exactly 10 or 13
- Duplicate ISBN is rejected
- ISBN checksum validation is not implemented in B5-2
- ISBN-10 `X` suffix is not allowed in B5-2

Allowed examples:

```text
9788979140630
978-89-7914-063-0
0132350882
0-13-235088-2
```

Rejected examples:

```text
0
1
2.0
0.2
1234
abc
978.123
-9788979140630
9788979140630-
978--8979140630
978 8979140630
```

### published_year

- Required
- Integer only
- Must be between `1000` and current year + 1

### price

- Required
- Numeric only
- Must be finite
- Must be `0` or greater
- Must be less than or equal to `10000000`
- Allows at most 2 decimal places

### stock_quantity

- Required
- Integer only
- Must be `0` or greater
- Must be less than or equal to `100000`
- Extremely large values are rejected before reaching SQLite

### author_id / category_id

- Required
- Integer only
- Must reference existing `Author` / `Category`

## Manual Test Checklist

Run the app:

```bash
source .venv/bin/activate
uvicorn app.main:app --reload
```

Checklist:

- `GET /` returns the home page
- `GET /books` returns the book list page
- `GET /books/new` returns the create form
- Creating a valid book redirects with `303`
- Blank title shows an HTML form error
- Invalid ISBN shows an HTML form error
- Duplicate ISBN shows an HTML form error
- Invalid year shows an HTML form error
- Invalid price shows an HTML form error
- Invalid stock quantity shows an HTML form error
- Book detail page shows title, author name, category name, year, price, stock, ISBN
- Edit form preserves current book values
- Updating a valid book redirects with `303`
- Delete form submits by POST and redirects with `303`
- `GET /books/{book_id}/delete` is not a valid delete flow
- Missing integer book detail/edit pages render HTML `404` with `Book not found.`
- Invalid path parameters render HTML `422` with `Invalid request.`
- Search by title filters book list

Lightweight import check:

```bash
.venv/bin/python -c "import app.main; print(app.main.app.title)"
```

Expected output:

```text
B5-2 Book CRUD
```

## Notes

- `library.db` is created during app execution or verification.
- `library.db` is intentionally ignored by `.gitignore`.
- `.venv/`, `__pycache__/`, `*.pyc`, and `*.db` are ignored.
- No authentication exists in B5-2.
- No members or loans are implemented in B5-2.
- No borrow/return or overdue workflow is implemented in B5-2.
