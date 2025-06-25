# Stonkie Backend

Welcome to the **Stonkie Backend** project! This repository contains the backend code for Stonkie, a platform for financial data analysis and reporting.

## Table of Contents

- [Features](#features)
- [Project Structure](#project-structure)
- [Requirements](#requirements)
- [Setup Instructions](#setup-instructions)
  - [1. Clone the Repository](#1-clone-the-repository)
  - [2. Create a Virtual Environment](#2-create-a-virtual-environment)
  - [3. Install Dependencies](#3-install-dependencies)
  - [4. Set Up Environment Variables](#4-set-up-environment-variables)
  - [5. Database Setup & Migrations](#5-database-setup--migrations)
  - [6. Running the Application](#6-running-the-application)
- [Scripts](#scripts)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

---

## Features

- Financial data ingestion and analysis
- Company insights and reporting
- Integration with AI models (OpenAI, Gemini)
- Database migrations with Alembic

## Project Structure

```
backend/
  agent/                  # Agent logic
  ai_models/              # AI model integrations
  alembic/                # Database migrations
  connectors/             # Data connectors (DB, PDF, etc.)
  external_knowledge/     # External data sources
  models/                 # SQLAlchemy models
  scripts/                # Utility scripts
  services/               # Business logic/services
  analyzer.py             # Main analyzer script
  constants.py            # Project constants
  faq_generator.py        # FAQ generation logic
  main.py                 # Entry point
  requirements.txt        # Python dependencies
  README.md               # Project documentation
```

## Requirements

- Python 3.8 or higher
- [pip](https://pip.pypa.io/en/stable/)
- (Recommended) [virtualenv](https://virtualenv.pypa.io/en/latest/) or [venv](https://docs.python.org/3/library/venv.html)
- (Optional) PostgreSQL or your preferred database

## Setup Instructions

### 1. Clone the Repository

```bash
git clone https://github.com/yourusername/stonkie-backend.git
cd stonkie-backend/backend
```

### 2. Create a Virtual Environment

It's best to use a virtual environment to manage dependencies.

**Using venv:**

```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 3. Install Dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Set Up Environment Variables

Create a `.env` file in the `backend/` directory to store sensitive information (API keys, DB credentials, etc.).  
Example:

```
DATABASE_URL=postgresql://user:password@localhost:5432/stonkie
OPENAI_API_KEY=your_openai_key
GEMINI_API_KEY=your_gemini_key
```

> **Note:** The actual variables required depend on your code. Check for usage of `os.environ` or similar in the codebase.

### 5. Database Setup & Migrations

**Initialize the database:**

- Make sure your database server is running and accessible.
- Update `DATABASE_URL` in your `.env` file as needed.

**Run Alembic migrations:**

```bash
alembic upgrade head
```

This will apply all database migrations.

### 6. Running the Application

The main entry point is likely `main.py`. To run:

```bash
python main.py
```

> Check `main.py` for any required arguments or configuration.

---

## Scripts

There are several utility scripts in the `scripts/` directory.  
For example:

- `export_annual_financial_report.py`
- `export_financial_report.py`
- `migrate_financial_statement.py`

Run them as:

```bash
python scripts/export_annual_financial_report.py
```

---

## Testing

If you have tests, run them as follows (assuming you use `pytest`):

```bash
pip install pytest
pytest
```

---

## Troubleshooting

- **Module Not Found:** Ensure your virtual environment is activated.
- **Database Connection Errors:** Check your `DATABASE_URL` and that your DB server is running.
- **Missing Environment Variables:** Ensure your `.env` file is set up and loaded.

---

## Contributing

1. Fork the repository
2. Create a new branch (`git checkout -b feature/your-feature`)
3. Commit your changes
4. Push to the branch (`git push origin feature/your-feature`)
5. Open a Pull Request

---

## License

This project is licensed under the MIT License.

---

**Need help?**  
Open an issue or contact the maintainer.
