# 🚀 AutoApply - Internship Hunter Bot

## Overview
**AutoApply** is a specialized automation tool designed to help students and job seekers find and apply for internships efficiently. It automates the process of discovering companies (specifically startups and tech firms), finding their direct contact information, and preparing personalized cold emails.

Built with **Python** and **Streamlit**, this dashboard serves as your command center for your internship search in Europe (Paris, Barcelona, Berlin, etc.).

## ✨ Features
*   **🔍 Smart Scraping:** Automatically searches the web for companies matching your criteria (e.g., "Data Science Startup Paris").
*   **📧 Email Discovery:** Extracts contact emails (`contact@`, `jobs@`, `hr@`) from company websites using intelligent parsing.
*   **🛡️ Anti-Spam Filtering:** Automatically ignores junk emails and generic domains to ensure high-quality leads.
*   **📝 Template Management:** Generates professional, localized email drafts (French/English) based on the target country.
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

### 3. Configure Environment
Create a `.env` file in the root directory (if not already present) and add your email credentials:
```ini
MY_EMAIL=your_email@gmail.com
MY_APP_PASSWORD=your_google_app_password
```
*> **Note:** You need to generate an "App Password" in your Google Account settings (Security > 2-Step Verification > App passwords). Do not use your regular login password.*

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
    *   **Important:** The app is currently in "Demo Mode" for sending (it simulates sending). To enable real sending, uncomment the `send_email` line in `app.py`.

## ⚠️ Disclaimer
This tool is for personal use. Please respect website Terms of Service and anti-spam regulations (GDPR in Europe). Do not use this for mass marketing or spamming.
