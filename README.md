# 🚀 AutoApply - Internship Hunter Bot

## Overview
**AutoApply** is a specialized automation tool designed to help students and job seekers find and apply for internships efficiently. It automates the process of discovering companies (specifically startups and tech firms), finding their direct contact information, and preparing personalized cold emails.

Built with **Python** and **Streamlit**, this dashboard serves as your command center for your internship search in Europe (Paris, Barcelona, Berlin, etc.).

## ✨ Features
*   **🔍 Smart Scraping:** automatically searches the web for companies matching your criteria. It now uses improved logic to detect accurate company names and filter out aggregation sites.
*   **📧 Email Discovery:** Extracts contact emails (`contact@`, `jobs@`, `hr@`) using intelligent parsing and validation.
*   **🛡️ Strong Filtering:** Automatically ignores junk emails (w3.org, sentry.io, etc.) and filters out generic lists to ensure high-quality leads.
*   **⚙️ Fully Configurable:** Customize your profile, CVs, and email templates directly via `.env` without touching the code.
*   **📝 Dynamic Templates:** Supports placeholder variables (`{company_name}`, `{signature}`) for personalized outreach in multiple languages.
*   **📊 Lead Management:** Tracks the status of your applications (Pending, Sent) in a clean, editable table.

## 🛠️ Installation & Setup

### Prerequisites
*   Python 3.8 or higher
*   A Gmail account (for sending emails)

### 1. Clone or Download
Download this project to your local machine.

### 2. Install Dependencies
Open a terminal in the project folder and run:
```bash
pip install -r requirements.txt
```
Or create a virtual environement if you are on linux.

### 3. Configure Environment
This project uses a `.env` file to manage your personal details, credentials, and email templates. This ensures your data remains private and easily editable.

1.  **Copy the example file:**
    ```bash
    cp .env.example .env
    ```
2.  **Edit `.env` with your details:**
    Open the `.env` file and fill in the following:

    *   **Gmail Credentials:**
        ```ini
        MY_EMAIL=your_email@gmail.com
        MY_APP_PASSWORD=xxxx xxxx xxxx xxxx
        ```
        *> **Note:** You need to generate an "App Password" in your Google Account settings (Security > 2-Step Verification > App passwords).*

    *   **Applicant Details:** (Used in your email signature)
        ```ini
        APPLICANT_NAME=John Doe
        APPLICANT_PHONE=+1 234 567 890
        APPLICANT_WEBSITE=www.johndoe.com
        APPLICANT_LINKEDIN=https://linkedin.com/in/johndoe
        ```

    *   **CV Paths:** (Point to your PDF files)
        ```ini
        CV_FILE_FR=docs/My_French_CV.pdf
        CV_FILE_EN=docs/My_English_CV.pdf
        ```

    *   **Email Templates:**
        You can fully customize the subject and body of your emails in the `.env` file. Use the placeholders `{company_name}` and `{signature}` to dynamically insert data.
        ```ini
        EMAIL_SUBJECT_EN=Application for {company_name}
        EMAIL_BODY_EN="Hello, I would like to work at {company_name}...\n\n{signature}"
        ```

## 🚀 How to Run
To launch the dashboard, open your terminal and run:

```bash
streamlit run app.py
```

The application will open automatically in your default web browser (usually at `http://localhost:8501`).

## 💡 Usage Guide

1.  **Auto-Scrape:**
    *   Go to the "Auto-Scrape Leads" section.
    *   Enter a query like *"FinTech startup Paris contact email"* or *"AI company Berlin jobs"*.
    *   Adjust the number of pages to scan.
    *   Click **Start Scraping** and watch the bot find leads.

2.  **Review Leads:**
    *   Check the table for found companies.
    *   You can manually add leads if you find them elsewhere.

3.  **Send Emails:**
    *   Use the "Email Operations" section to send batches of emails.
    *   Select the companies you want to contact and click "Send Email".
    *   The app will automatically attach the correct CV (French or English) based on the country.

## ⚠️ Disclaimer
This tool is for personal use. Please respect website Terms of Service and anti-spam regulations (GDPR in Europe). Do not use this for mass marketing or spamming.
