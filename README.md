# Stonkie Backend

Welcome to the **Stonkie Backend** project! This repository contains the backend code for Stonkie, a platform for financial data analysis and reporting.

## Table of Contents

- [Features](#features)
- [Project Structure](#project-structure)
- [Requirements](#requirements)
- [Setup Instructions](#setup-instructions)
  - [1. Clone the Repository](#1-clone-the-repository)
  - [2. Install Python with asdf (Recommended)](#2-install-python-with-asdf-recommended)
  - [3. Create a Virtual Environment](#3-create-a-virtual-environment)
  - [4. Install Dependencies](#4-install-dependencies)
  - [5. Set Up Environment Variables](#5-set-up-environment-variables)
  - [6. Database Setup & Migrations](#6-database-setup--migrations)
  - [7. Running the Application](#7-running-the-application)
- [Scripts](#scripts)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

---

## Features

- **AI-Driven Financial Analysis:** Harnesses advanced AI models to deeply analyze company business operations, financial statements, and market trends.
- **Automated Company Insights & Reporting:** Instantly generates actionable insights and comprehensive reports tailored to each company.
- **Seamless AI Integrations:** Leverages the power of OpenAI and Gemini for natural language understanding, data extraction, and intelligent recommendations.
- **Flexible Data Ingestion:** Easily connect and process financial data from multiple sources for a holistic view.

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

## Setup Instructions

### 1. Clone the Repository

```bash
git clone https://github.com/yourusername/stonkie-backend.git
cd stonkie-backend/backend
```

### 2. Install Python with asdf (Recommended)

This project uses [asdf](https://asdf-vm.com/) to manage the Python version. Ensure you have asdf installed:

```bash
# Install asdf if you don't have it
# See: https://asdf-vm.com/guide/getting-started.html
```

Add the Python plugin and install the required version:

```bash
asdf plugin add python || true
asdf install python $(cat .tool-versions | grep python | awk '{print $2}')
```

Verify that your Python version matches the one specified in `.tool-versions`:

```bash
python --version  # Should match the version in .tool-versions
```

### 3. Create a Virtual Environment

It's best to use a virtual environment to manage dependencies.

**Using venv:**

```bash
python -m venv venv
source venv/bin/activate
```

### 4. Install Dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 5. Set Up Environment Variables

Create a `.env` file in the `backend/` directory to store sensitive information (API keys, DB credentials, etc.).  
Example:

```
DATABASE_URL=postgresql://user:password@localhost:5432/stonkie
OPENAI_API_KEY=your_openai_key
GEMINI_API_KEY=your_gemini_key
```

> **Note:** The actual variables required depend on your code. Check for usage of `os.environ` or similar in the codebase.

### 6. Database Setup & Migrations

**Database:** This project uses **PostgreSQL** as its database.

#### Getting PostgreSQL Up and Running

- **For macOS:**
  - Install with Homebrew:
    ```bash
    brew install postgresql
    brew services start postgresql
    ```
  - By default, this will start a local PostgreSQL server.

**Initialize the database:**

- Make sure your database server is running and accessible.
- Update `DATABASE_URL` in your `.env` file as needed.

**Run Alembic migrations:**

```bash
alembic upgrade head
```

This will apply all database migrations.

### 7. Running the Application

```bash
hypercorn main:app --bind localhost:8080 --reload
```

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
Will come at some point 😉

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
