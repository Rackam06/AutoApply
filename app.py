import os
from dotenv import load_dotenv
import streamlit as st
import pandas as pd
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from datetime import datetime
import time
import requests
from bs4 import BeautifulSoup
import re
# from googlesearch import search # Deprecated/Broken
from ddgs import DDGS
from urllib.parse import urlparse

# --- CONFIGURATION ---
load_dotenv()
MY_EMAIL = os.getenv("MY_EMAIL")
MY_APP_PASSWORD = os.getenv("MY_APP_PASSWORD")
CSV_FILE = "leads.csv"

# --- DATA LOADING ---
if 'leads' not in st.session_state:
    if os.path.exists(CSV_FILE):
        st.session_state.leads = pd.read_csv(CSV_FILE)
    else:
        st.session_state.leads = pd.DataFrame(columns=["Company", "Email", "Country", "Status"])

def save_leads():
    st.session_state.leads.to_csv(CSV_FILE, index=False)

# --- SCRAPING FUNCTION ---
def extract_emails_from_html(soup):
    emails = set()
    # 1. Look for mailto links (most reliable)
    for a in soup.find_all('a', href=True):
        if a['href'].startswith('mailto:'):
            email = a['href'].replace('mailto:', '').split('?')[0]
            emails.add(email)
            
    # 2. Regex search in visible text
    text_content = soup.get_text(" ", strip=True)
    found = re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text_content)
    emails.update(found)
    
    # 3. Filter junk
    junk_domains = ['example.com', 'w3.org', 'sentry.io', 'u-paris.fr', 'google.com', 'linkedin.com', 'twitter.com', 'facebook.com']
    junk_extensions = ['.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp']
    
    valid_emails = []
    for e in emails:
        e = e.lower().strip()
        if not any(d in e for d in junk_domains) and not any(e.endswith(ext) for ext in junk_extensions):
            valid_emails.append(e)
            
    return list(set(valid_emails))

def find_emails_from_web(query, num_results=10):
    found_leads = []
    try:
        # Search Google
        urls = search(query, num_results=num_results)
        
        for url in urls:
            try:
                # Skip common non-company sites to save time
                if any(x in url for x in ['linkedin.com', 'indeed.com', 'glassdoor.com', 'welcometothejungle.com']):
                    continue

                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                }
                
                # 1. Scrape Main Page
                response = requests.get(url, headers=headers, timeout=10)
                if response.status_code != 200:
                    continue
                    
                soup = BeautifulSoup(response.text, 'html.parser')
                valid_emails = extract_emails_from_html(soup)
                
                # 2. If no emails, try to find "Contact" or "About" page
                if not valid_emails:
                    from urllib.parse import urljoin
                    
                    # Look for links containing specific keywords
                    contact_links = soup.find_all('a', href=True)
                    for link in contact_links:
                        href = link['href'].lower()
                        text = link.get_text().lower()
                        
                        if any(x in href or x in text for x in ['contact', 'about', 'team', 'nous-contacter', 'a-propos']):
                            contact_url = urljoin(url, link['href'])
                            try:
                                resp_contact = requests.get(contact_url, headers=headers, timeout=10)
                                if resp_contact.status_code == 200:
                                    soup_contact = BeautifulSoup(resp_contact.text, 'html.parser')
                                    valid_emails.extend(extract_emails_from_html(soup_contact))
                                    if valid_emails:
                                        break # Stop if we found something
                            except:
                                continue

                if valid_emails:
                    # Guess company name from Title
                    company_name = "Unknown"
                    if soup.title:
                        company_name = soup.title.string.strip()[:30]
                    
                    # Try to infer country from TLD
                    country = "International"
                    if ".fr" in url:
                        country = "France"
                    elif ".de" in url:
                        country = "Germany"
                        
                    for email in valid_emails:
                        found_leads.append({
                            "Company": company_name,
                            "Email": email,
                            "Country": country,
                            "Status": "Pending"
                        })
            except Exception as e:
                # st.warning(f"Could not scrape {url}: {e}")
                continue
                
    except Exception as e:
        st.error(f"Search failed: {e}")
        
    return found_leads

# --- EMAIL TEMPLATES ---
def get_email_content(company_name, country):
    # DYNAMIC CONTENT BASED ON RESUME
    # Projects: Redaking (LLM) , SynapsX (Finance) 
    # Education: Double MSc Data Science/FinTech 
    
    if country.lower() == "france":
        subject = f"Candidature Spontanée - Data Scientist / FinTech (Master 2) - {company_name}"
        body = f"""
Bonjour,

Actuellement étudiant en Double Master Data Science & FinTech (Université de Rennes & Université de Trento), je me permets de vous contacter pour une candidature spontanée en tant que Data Scientist ou Développeur Logiciel pour mon stage de fin d'études (début février 2026).

Votre expertise chez {company_name} m'intéresse particulièrement. De mon côté, j'ai acquis de solides bases techniques (Python, C, SQL) et développé plusieurs projets concrets :

- Redaking Project : Développement d'un assistant LLM local (Python + APIs + Ollama) avec gestion de mémoire persistante.
- SynapsX : Création d'un outil de recommandation d'investissement boursier basé sur des algorithmes de prédiction.
- Développement Web** : J'ai également travaillé sur le développement Fullstack lors d'un précédent poste.

Je serais ravi de pouvoir échanger avec vous sur la manière dont je pourrais contribuer aux projets de {company_name}. Vous trouverez mon CV en pièce jointe.

Bien cordialement,

Wail Ameur
+33 6 95 02 72 99
www.wailameur.com
"""
    else:
        subject = f"Internship Application - Data Scientist / FinTech (Final Year MSc) - {company_name}"
        body = f"""
Dear Hiring Manager,

I am currently a Double Master’s student in Data Science & FinTech (University of Rennes & University of Trento), writing to express my interest in a Data Scientist or Software Developer internship at {company_name} starting February 2026.

I have built a strong technical foundation in Python, C, and SQL, applying these skills in complex projects:

- Redaking Project: Developed a local LLM assistant (Python + APIs + Ollama) capable of file management and persistent memory.
- SynapsX: Built software for stock market investment recommendations using prediction algorithms.
- Web Development: Previous experience in Fullstack development (PHP/JS).

I am eager to bring my background in applied AI and financial analysis to the team at {company_name}. My resume is attached.

Best regards,

Wail Ameur
+33 6 95 02 72 99
www.wailameur.com
"""
    return subject, body

# --- SENDING FUNCTION ---
def send_email(to_email, subject, body, attachment_path=None):
    msg = MIMEMultipart()
    msg['From'] = MY_EMAIL
    msg['To'] = to_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))
    
    # Attachment Logic
    if attachment_path and os.path.exists(attachment_path):
        try:
            with open(attachment_path, "rb") as f:
                part = MIMEApplication(f.read(), Name=os.path.basename(attachment_path))
            # After the file is closed
            part['Content-Disposition'] = f'attachment; filename="{os.path.basename(attachment_path)}"'
            msg.attach(part)
        except Exception as e:
            st.error(f"Failed to attach file: {e}")

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(MY_EMAIL, MY_APP_PASSWORD)
        text = msg.as_string()
        server.sendmail(MY_EMAIL, to_email, text)
        server.quit()
        return True
    except Exception as e:
        st.error(f"Error sending to {to_email}: {e}")
        return False

# --- DASHBOARD UI ---
st.title("🚀 Internship Hunter Dashboard")
st.subheader("Data Science & FinTech Application Bot")

# 0. Automatic Scraping
with st.expander("🔍 Auto-Scrape Leads from Web", expanded=True):
    st.info("💡 Tip: Try queries like 'Data Science startup Paris contact' or 'FinTech recruitment London email'.")
    
    c1, c2 = st.columns([3, 1])
    search_query = c1.text_input("Search Query", "startup data science Paris contact email")
    num_pages = c2.number_input("Max Results", min_value=5, max_value=50, value=10)
    
    debug_mode = st.checkbox("Show Debug Logs (See what the bot is doing)")
    
    start_scraping = st.button("Start Scraping", type="primary")

if start_scraping:
    status_container = st.status("Initializing search...", expanded=True)
    log_container = st.container()
    
    with status_container as s:
        found_leads = []
        try:
            s.write(f"🔍 Searching DuckDuckGo for: '{search_query}'...")
            # Search DuckDuckGo
            try:
                # DDGS().text returns a generator of dicts {'href':..., 'title':..., 'body':...}
                # Use region='fr-fr' to prioritize French results if the query implies it, 
                # but let's make it dynamic or just default to 'wt-wt' (world) if not specified.
                # However, user specifically asked for better results for Paris.
                region = 'fr-fr' if 'paris' in search_query.lower() or 'france' in search_query.lower() else 'wt-wt'
                
                results = list(DDGS().text(search_query, region=region, max_results=num_pages))
                urls = [r['href'] for r in results]
                
                s.write(f"✅ Found {len(urls)} URLs. Starting analysis...")
            except Exception as e:
                s.error(f"Search failed: {e}")
                urls = []

            progress_bar = s.progress(0)
            
            for i, url in enumerate(urls):
                progress_bar.progress((i + 1) / len(urls))
                
                # Skip common non-company sites
                if any(x in url for x in ['linkedin.com', 'indeed.com', 'glassdoor.com', 'welcometothejungle.com', 'youtube.com', 'facebook.com']):
                    if debug_mode: log_container.text(f"⏭️ Skipping aggregator: {url}")
                    continue

                if debug_mode: log_container.text(f"🌐 Visiting: {url}")
                
                try:
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                    }
                    
                    # 1. Scrape Main Page
                    try:
                        response = requests.get(url, headers=headers, timeout=10)
                    except:
                        if debug_mode: log_container.text(f"❌ Connection failed: {url}")
                        continue
                        
                    if response.status_code != 200:
                        continue
                        
                    soup = BeautifulSoup(response.text, 'html.parser')
                    valid_emails = extract_emails_from_html(soup)
                    
                    # 2. If no emails, try to find "Contact" or "About" page
                    if not valid_emails:
                        # ... (existing contact page logic) ...
                        from urllib.parse import urljoin
                        contact_links = soup.find_all('a', href=True)
                        for link in contact_links:
                            href = link['href'].lower()
                            text = link.get_text().lower()
                            
                            if any(x in href or x in text for x in ['contact', 'about', 'team', 'nous-contacter', 'a-propos']):
                                contact_url = urljoin(url, link['href'])
                                if debug_mode: log_container.text(f"  ↳ Checking sub-page: {contact_url}")
                                try:
                                    resp_contact = requests.get(contact_url, headers=headers, timeout=10)
                                    if resp_contact.status_code == 200:
                                        soup_contact = BeautifulSoup(resp_contact.text, 'html.parser')
                                        new_emails = extract_emails_from_html(soup_contact)
                                        valid_emails.extend(new_emails)
                                        if new_emails:
                                            break 
                                except:
                                    continue

                    if valid_emails:
                        s.write(f"🎉 Found {len(valid_emails)} email(s) at {url}")
                        
                        # Better Company Name Extraction
                        company_name = "Unknown"
                        
                        # 1. Try Open Graph Site Name
                        og_site_name = soup.find("meta", property="og:site_name")
                        if og_site_name and og_site_name.get("content"):
                            company_name = og_site_name["content"].strip()
                        else:
                            # 2. Try Domain Name (cleaner than Title)
                            domain = urlparse(url).netloc
                            # Remove www. and .com/.fr etc
                            if domain.startswith("www."):
                                domain = domain[4:]
                            company_name = domain.split('.')[0].capitalize()
                        
                        # Try to infer country from TLD
                        country = "International"
                        if ".fr" in url:
                            country = "France"
                        elif ".de" in url:
                            country = "Germany"
                            
                        for email in valid_emails:
                            found_leads.append({
                                "Company": company_name,
                                "Email": email,
                                "Country": country,
                                "Status": "Pending"
                            })
                except Exception as e:
                    if debug_mode: log_container.text(f"⚠️ Error processing {url}: {e}")
                    continue
            
            s.update(label="Scraping completed!", state="complete", expanded=False)
            
        except Exception as e:
            st.error(f"Search failed: {e}")
        
        if found_leads:
            new_df = pd.DataFrame(found_leads)
            # Remove duplicates
            new_df = new_df.drop_duplicates(subset=['Email'])
            st.session_state.leads = pd.concat([st.session_state.leads, new_df], ignore_index=True).drop_duplicates(subset=['Email'])
            save_leads() # Save to CSV
            st.success(f"Found {len(found_leads)} new leads!")
        else:
            st.warning("No emails found. Try a different query or increase 'Max Results'.")

# 1. Add New Lead
with st.expander("Add New Lead Manually"):
    c1, c2, c3 = st.columns(3)
    new_company = c1.text_input("Company Name")
    new_email = c2.text_input("Email Address")
    new_country = c3.selectbox("Country", ["France", "International"])
    if st.button("Add Lead"):
        new_row = {"Company": new_company, "Email": new_email, "Country": new_country, "Status": "Pending"}
        st.session_state.leads = pd.concat([st.session_state.leads, pd.DataFrame([new_row])], ignore_index=True)
        save_leads()
        st.success("Lead added!")

# 2. View Data
st.dataframe(st.session_state.leads)

# 3. Control Center
st.divider()
st.write("### Email Operations")

daily_limit = st.slider("Daily Limit", 1, 10, 5)

if st.button(f"Send Batch ({daily_limit} emails)"):
    pending_leads = st.session_state.leads[st.session_state.leads["Status"] == "Pending"].head(daily_limit)
    
    if pending_leads.empty:
        st.warning("No pending leads found!")
    else:
        progress_bar = st.progress(0)
        for index, row in pending_leads.iterrows():
            subj, body = get_email_content(row['Company'], row['Country'])
            
            # Determine Attachment
            if row['Country'] == "France":
                attachment = "docs/Wail_Ameur_CV.pdf"
            else:
                attachment = "docs/Wail_Ameur_Resume.pdf"
            
            # Send Email
            success = send_email(row['Email'], subj, body, attachment_path=attachment) 
            
            if success:
                st.session_state.leads.at[index, 'Status'] = f"Sent {datetime.now().strftime('%Y-%m-%d')}"
                st.toast(f"Sent to {row['Company']}")
            else:
                st.toast(f"Failed to send to {row['Company']}")
            
            progress_bar.progress((index + 1) / len(pending_leads))
            time.sleep(1) # Small delay to be nice to Gmail server
            
        save_leads() # Save status updates
        st.success("Batch completed!")
        st.rerun()

# 4. Preview Template
st.divider()
st.write("### Template Preview")
preview_country = st.radio("Preview Language", ["France", "International"])
p_subj, p_body = get_email_content("TechCorp", preview_country)
st.text_area("Subject", p_subj)
st.text_area("Body", p_body, height=300)