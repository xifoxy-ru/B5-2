# Book CRUD 웹 애플리케이션

## 프로젝트 소개

FastAPI로 만든 도서 관리 웹 애플리케이션입니다. Jinja2로 HTML을 서버 사이드 렌더링하고, SQLAlchemy ORM과 SQLite를 사용해 데이터를 저장합니다. 도서 등록, 목록 및 상세 조회, 수정, 삭제와 제목 검색을 제공합니다.

## 주요 기능

- Book 목록 조회 및 제목 검색
- Book 등록, 상세 조회, 수정, 삭제
- Author와 Category 문자열 입력
- 입력값 정규화와 필드별 Validation
- HTML 형식의 404, 405, 422 오류 화면
- 등록, 수정, 삭제 후 `303 See Other` Redirect

## 기술 스택

- Python
- FastAPI 0.139.0
- Uvicorn 0.51.0
- SQLAlchemy 2.0.51
- Jinja2 3.1.6
- SQLite
- python-multipart 0.0.32

## 프로젝트 구조

```text
app/
├── main.py                 # 애플리케이션 생성과 구성 요소 등록
├── config.py               # 애플리케이션 설정값과 메시지
├── database.py             # Engine, Session, DB 초기화와 SQLite 연결 설정
├── exception_handlers.py   # HTML 오류 응답 처리
├── template_config.py      # Jinja2 Template 설정
├── models/                 # Book SQLAlchemy 테이블 정의
├── repositories/           # 데이터 조회와 저장
├── routers/                # HTTP 요청, Form, 응답과 Redirect 처리
├── schemas/                # Book Form 입력 데이터 구조
├── services/               # 업무 흐름, Validation과 정규화
└── templates/              # 서버에서 렌더링하는 HTML Template
scripts/
└── regression_check.py     # 직접 실행하는 회귀 검증 스크립트
requirements.txt            # Python Dependency 목록
library.db                  # SQLite 데이터베이스 파일
```

## 실행 구조

```text
HTTP/Form
-> Router
-> Service
-> Repository
-> SQLAlchemy ORM / SQLite

Router
-> Jinja2 Template
```

- Router는 요청과 Form을 받고 응답 또는 Redirect를 반환합니다.
- 중앙 Router는 Home Router와 `/books` prefix를 사용하는 Book Router를 조립합니다.
- Service는 입력값 Validation, 정규화와 Book 업무 흐름을 담당합니다.
- Repository는 SQLAlchemy ORM을 통한 데이터 조회와 저장을 담당합니다.
- Model은 독립된 Book 테이블을 정의합니다.
- Template은 Router가 준비한 데이터를 HTML 화면으로 렌더링합니다.

## 설치

프로젝트 루트에서 가상환경을 만들고 Dependency를 설치합니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## 실행

가상환경을 활성화한 경우:

```bash
uvicorn app.main:app --reload
```

가상환경을 활성화하지 않은 경우:

```bash
.venv/bin/uvicorn app.main:app --reload
```

## 접속 주소

- 홈: <http://127.0.0.1:8000/>
- Book 목록: <http://127.0.0.1:8000/books>
- API 문서: <http://127.0.0.1:8000/docs>

## 주요 Route

| Method | Path | 설명 |
|---|---|---|
| `GET` | `/` | 홈 화면 |
| `GET` | `/books` | Book 목록과 제목 검색 |
| `GET` | `/books/new` | Book 등록 Form |
| `POST` | `/books` | Book 등록 |
| `GET` | `/books/{book_id}` | Book 상세 조회 |
| `GET` | `/books/{book_id}/edit` | Book 수정 Form |
| `POST` | `/books/{book_id}/edit` | Book 수정 |
| `POST` | `/books/{book_id}/delete` | Book 삭제 |

검색은 `GET /books?q=검색어` 형식을 사용합니다. 삭제는 POST 방식만 허용하며, 등록과 수정 성공 후 해당 Book 상세 화면으로, 삭제 성공 후 Book 목록으로 `303` Redirect합니다.

## 입력 Validation

- 제목은 필수입니다. 앞뒤 공백을 제거하고 연속 공백을 하나로 정리하며, 최대 200자까지 허용합니다.
- Author와 Category는 Book에 직접 저장하는 필수 문자열입니다. 앞뒤 공백을 제거하며 각각 최대 100자까지 허용합니다.
- 출판 연도는 1000 이상, 실행 시점의 현재 연도보다 1년 뒤까지 허용합니다.
- 가격은 0 이상 10,000,000 이하이며 소수점 둘째 자리까지 허용합니다. `NaN`, `Infinity`, `-Infinity`는 허용하지 않습니다.
- 재고는 0 이상 100,000 이하의 정수여야 합니다.
- ISBN-10과 ISBN-13 입력을 지원하며 숫자 그룹 사이의 단일 하이픈을 허용합니다. 내부 공백, 시작·끝 하이픈과 연속 하이픈은 허용하지 않습니다.
- ISBN-10과 ISBN-13의 체크디지트를 검증하고, ISBN-13은 `978` 또는 `979` prefix인지 확인합니다.
- 유효한 ISBN-10은 대응하는 ISBN-13으로 변환하며, DB에는 하이픈 없는 13자리 숫자 canonical ISBN만 저장합니다.
- 입력 표현이 달라도 canonical ISBN이 같으면 중복으로 처리합니다. ISBN 전체 입력은 최대 20자입니다.
- 국제 ISBN 형식만 검증하며 실제 발급 여부, 실존 도서 여부 또는 외부 ISBN 등록 정보는 확인하지 않습니다.
- Validation 오류가 발생하면 같은 Form 화면에 원래 입력값과 필드별 오류 메시지를 표시합니다.

## 데이터베이스

- SQLite 파일은 프로젝트 루트의 `library.db`입니다.
- 사용자 테이블은 SQLAlchemy ORM의 `books` 하나입니다.
- Author와 Category는 별도 Model이나 테이블이 아니라 Book의 문자열 Column입니다.
- Book은 다른 사용자 정의 Model과 Foreign Key 또는 relationship을 사용하지 않습니다.
- SQLite 연결이 생성될 때마다 기존 인프라 설정인 `PRAGMA foreign_keys=ON`을 적용합니다.
- 애플리케이션 시작 시 필요한 `books` 테이블을 생성합니다.

## 오류 처리

- 존재하지 않는 페이지는 HTML `404` 화면으로 응답합니다.
- 허용하지 않는 HTTP Method는 HTML `405` 화면으로 응답합니다.
- 잘못된 Path Parameter는 HTML `422` 화면으로 응답합니다.
- Form Validation 오류는 같은 Form 화면을 `200`으로 다시 렌더링하고 필드별 오류를 표시합니다.

## 회귀 검증

다음 명령으로 전체 회귀 검증을 한 번에 실행합니다.

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/regression_check.py
```

회귀 스크립트는 다음 항목을 확인합니다.

- Route, CRUD와 제목 검색
- Redirect와 HTML 오류 화면
- 입력값 Validation과 원래 입력값 보존
- Book 단일 모델 구조와 ISBN Unique 제약
- 실제 `library.db`의 존재 여부, 크기와 수정 시간 유지

검증 데이터는 격리된 임시 SQLite DB에 저장되며 실행이 끝나면 정리됩니다.
