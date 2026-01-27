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

# Applicant Details (Defaults to placeholders if not in .env)
APPLICANT_NAME = os.getenv("APPLICANT_NAME", "Jane Doe")
APPLICANT_PHONE = os.getenv("APPLICANT_PHONE", "+1 234 567 890")
APPLICANT_WEBSITE = os.getenv("APPLICANT_WEBSITE", "www.janedoe.com")
APPLICANT_LINKEDIN = os.getenv("APPLICANT_LINKEDIN", "")

# CV Files
CV_FILE_FR = os.getenv("CV_FILE_FR", "docs/CV_French.pdf")
CV_FILE_EN = os.getenv("CV_FILE_EN", "docs/CV_English.pdf")

# Email Templates
EMAIL_SUBJECT_FR = os.getenv("EMAIL_SUBJECT_FR", "Candidature Spontanée - {company_name}")
EMAIL_BODY_FR = os.getenv("EMAIL_BODY_FR", "Bonjour,\n\nJe postule chez {company_name}.\n\nCordialement,\n{signature}")

EMAIL_SUBJECT_EN = os.getenv("EMAIL_SUBJECT_EN", "Internship Application - {company_name}")
EMAIL_BODY_EN = os.getenv("EMAIL_BODY_EN", "Dear Manager,\n\nI am applying to {company_name}.\n\nBest regards,\n{signature}")

CSV_FILE = "leads.csv"

# --- DATA LOADING ---
if 'leads' not in st.session_state:
    if os.path.exists(CSV_FILE):
        st.session_state.leads = pd.read_csv(CSV_FILE)
    else:
        st.session_state.leads = pd.DataFrame(columns=["Select", "Company", "Email", "Country", "Status"])

# Ensure Select column exists and is boolean
if "Select" not in st.session_state.leads.columns:
    st.session_state.leads.insert(0, "Select", False)
else:
    st.session_state.leads["Select"] = st.session_state.leads["Select"].fillna(False).astype(bool)

def save_leads():
    if isinstance(st.session_state.leads, pd.DataFrame):
        st.session_state.leads.to_csv(CSV_FILE, index=False)

# --- SCRAPING FUNCTION ---
def extract_company_name(soup, url):
    """
    Robust way to find company name from a website.
    """
    candidates = []

    # 1. Open Graph Site Name (High Confidence)
    og_site_name = soup.find("meta", property="og:site_name")
    if og_site_name and og_site_name.get("content"):
        candidates.append(og_site_name["content"].strip())

    # 2. Schema.org Organization (High Confidence)
    import json
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            if not script.string: continue
            data = json.loads(script.string)
            if isinstance(data, dict):
                if data.get('@type') == 'Organization' and data.get('name'):
                    candidates.append(data['name'])
            elif isinstance(data, list):
                for item in data:
                    if item.get('@type') == 'Organization' and item.get('name'):
                        candidates.append(item['name'])
        except:
            pass
            
    # 3. Meta Title (Medium Confidence - often needs cleaning)
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
        # Common patterns: "Company Name - Home", "Welcome to Company Name", "Company Name | Tagline"
        separators = [' - ', ' | ', ' : ', ' – ']
        clean_title = title
        for sep in separators:
            parts = title.split(sep)
            if len(parts) > 1:
                # If the first part is short and looks like a name, take it
                if len(parts[0]) < 30:
                    clean_title = parts[0]
                # If the last part is the brand
                elif len(parts[-1]) < 30:
                    clean_title = parts[-1] 
        candidates.append(clean_title)
        
    # 4. Domain Name (Fallback / Baseline)
    domain_parts = urlparse(url).netloc.split('.')
    if domain_parts[0] == 'www':
        domain_name = domain_parts[1].capitalize()
    else:
        domain_name = domain_parts[0].capitalize()
    candidates.append(domain_name)
    
    # Selection Strategy
    # Return the first candidate that is not in a blacklist
    blacklist = ["Home", "Index", "Welcome", "Page", "En", "Ue", "De", "Fr", "Uk", "Us", "Web", "Site", "Unknown", "My Site", "WordPress"]
    
    for c in candidates:
        if c and c.strip() and c.strip() not in blacklist and len(c) > 2 and len(c) < 50:
            return c.strip()
            
    return domain_name

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
    junk_domains = [
        'example.com', 'w3.org', 'sentry.io', 'u-paris.fr', 'google.com', 'linkedin.com', 'twitter.com', 'facebook.com',
        'instagram.com', 'youtube.com', 'github.com', 'wordpress.org', 'cloudflare.com', 'medium.com'
    ]
    junk_extensions = ['.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.js', '.css']
    junk_prefixes = ['noreply', 'no-reply', 'admin', 'hostmaster', 'postmaster', 'privacy', 'webmaster']
    
    valid_emails = []
    for e in emails:
        e = e.lower().strip()
        
        # Syntax Check
        if len(e) < 5 or len(e) > 50: continue
        
        domain = e.split('@')[-1]
        
        if any(d in domain for d in junk_domains): continue
        if any(e.endswith(ext) for ext in junk_extensions): continue
        if any(e.startswith(p) for p in junk_prefixes): continue
        
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
    # DYNAMIC CONTENT FROM .ENV
    
    signature = f"\n{APPLICANT_NAME}\n{APPLICANT_PHONE}\n{APPLICANT_WEBSITE}\n{APPLICANT_LINKEDIN}"
    
    try:
        if country.lower() == "france":
            subject = EMAIL_SUBJECT_FR.replace("{company_name}", company_name)
            body = EMAIL_BODY_FR.replace("{company_name}", company_name).replace("{signature}", signature)
        else:
            subject = EMAIL_SUBJECT_EN.replace("{company_name}", company_name)
            body = EMAIL_BODY_EN.replace("{company_name}", company_name).replace("{signature}", signature)
    except Exception as e:
        subject = f"Application - {company_name}"
        body = f"Error generating email body: {e}"
        
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
                    
                    # --- SMART CRAWLING V2: STRICT MEDIA FILTERING ---
                    page_title = soup.title.string.lower() if soup.title else ""
                    url_lower = url.lower()
                    
                    # 1. Identify if this is a Media/News/Listicle site
                    # We do NOT want emails from these pages (usually press/editor emails)
                    # We ONLY want to follow links FROM these pages to the actual startups.
                    
                    listicle_keywords = ['top ', 'best ', 'list ', 'startups in', 'companies in', 'guide', 'blog', 'news', 'article', 'directory']
                    media_domains = [
                        'medium.com', 'forbes.com', 'techcrunch.com', 'venturebeat.com', 
                        'analyticsindiamag.com', 'analyticsinsight.net', 'fortune.com', 
                        'businessinsider.com', 'analyticsvidhya.com', 'builtin.com',
                        'clutch.co', 'goodfirms.co', 'capterra.com', 'g2.com', 'crunchbase.com'
                    ]
                    
                    is_media_site = any(d in url_lower for d in media_domains)
                    is_listicle_title = any(x in page_title for x in listicle_keywords)
                    
                    # Default: Scrape the page itself
                    target_urls = [url] 
                    
                    if is_media_site or is_listicle_title:
                        if debug_mode: log_container.text(f"  📰 Detected Media/Listicle. IGNORING emails on this page. Extracting external links...")
                        
                        # CLEAR target_urls so we don't scrape the news site itself
                        target_urls = []
                        
                        external_links = set()
                        for a in soup.find_all('a', href=True):
                            href = a['href']
                            try:
                                href_domain = urlparse(href).netloc
                                current_domain = urlparse(url).netloc
                                
                                # Must be a valid http link and NOT the same domain
                                if href.startswith('http') and href_domain and href_domain != current_domain:
                                    # Filter out social media, tech giants, and other noise
                                    noise_domains = [
                                        'linkedin', 'twitter', 'facebook', 'instagram', 'youtube', 
                                        'google', 'apple', 'microsoft', 'medium', 'wikipedia', 
                                        'amazon', 'cloudflare', 'whatsapp', 'telegram', 'tiktok'
                                    ]
                                    if not any(x in href for x in noise_domains):
                                        external_links.add(href)
                            except:
                                pass
                        
                        # Take top 10 external links to visit
                        if external_links:
                            # Prioritize links that look like root domains (e.g. "https://company.com/" vs "https://company.com/blog/post")
                            sorted_links = sorted(list(external_links), key=lambda x: len(urlparse(x).path))
                            target_urls = sorted_links[:10]
                            if debug_mode: log_container.text(f"  ➡️ Found {len(external_links)} external links. Visiting top {len(target_urls)} candidates...")
                        else:
                            if debug_mode: log_container.text(f"  ⚠️ No external links found on this listicle.")
                    
                    # --- PROCESS URLS ---
                    for target_url in target_urls:
                        try:
                            if target_url != url:
                                if debug_mode: log_container.text(f"    🌐 Visiting external: {target_url}")
                                try:
                                    resp_target = requests.get(target_url, headers=headers, timeout=10)
                                    if resp_target.status_code != 200: continue
                                    soup_target = BeautifulSoup(resp_target.text, 'html.parser')
                                except:
                                    continue
                            else:
                                soup_target = soup

                            valid_emails = extract_emails_from_html(soup_target)
                            
                            # 2. If no emails, try to find "Contact" or "About" page
                            if not valid_emails:
                                from urllib.parse import urljoin
                                contact_links = soup_target.find_all('a', href=True)
                                for link in contact_links:
                                    href = link['href'].lower()
                                    text = link.get_text().lower()
                                    
                                    if any(x in href or x in text for x in ['contact', 'about', 'team', 'nous-contacter', 'a-propos']):
                                        contact_url = urljoin(target_url, link['href'])
                                        # if debug_mode: log_container.text(f"      ↳ Checking sub-page: {contact_url}")
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
                                s.write(f"🎉 Found {len(valid_emails)} email(s) at {target_url}")
                                
                                # Better Company Name Extraction
                                company_name = extract_company_name(soup_target, target_url)
                                
                                # Try to infer country from TLD
                                country = "International"
                                if ".fr" in target_url:
                                    country = "France"
                                elif ".de" in target_url:
                                    country = "Germany"
                                    
                                for email in valid_emails:
                                    found_leads.append({
                                        "Select": False,
                                        "Company": company_name,
                                        "Email": email,
                                        "Country": country,
                                        "Status": "Pending"
                                    })
                        except Exception as e:
                            continue

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
        new_row = {"Select": False, "Company": new_company, "Email": new_email, "Country": new_country, "Status": "Pending"}
        st.session_state.leads = pd.concat([st.session_state.leads, pd.DataFrame([new_row])], ignore_index=True)
        save_leads()
        st.success("Lead added!")

# 2. View Data & Edit
st.write("### Manage Leads")
st.info("📝 You can edit the Company Name directly in the table below. Check the box to select companies for emailing.")

# Deduplication Button
if st.button("🧹 Clean Duplicates (Keep 1 per Company)"):
    # Keep the first entry for each company name, but prefer ones with 'Pending' status if possible
    st.session_state.leads = st.session_state.leads.sort_values('Status').drop_duplicates(subset=['Company'], keep='first').sort_index()
    save_leads()
    st.success("Duplicates removed! Kept 1 email per company.")
    st.rerun()

edited_df = st.data_editor(
    st.session_state.leads,
    column_config={
        "Select": st.column_config.CheckboxColumn(
            "Select",
            help="Select to send email",
            default=False,
        ),
        "Status": st.column_config.TextColumn(
            "Status",
            disabled=True
        )
    },
    disabled=["Status"],
    hide_index=True,
    key="leads_editor"
)

if not edited_df.equals(st.session_state.leads):
    st.session_state.leads = edited_df
    save_leads()
    st.rerun()

# 3. Control Center
st.divider()
st.write("### Email Operations")

# Filter selected rows
selected_indices = st.session_state.leads[st.session_state.leads["Select"]].index.tolist()
selected_count = len(selected_indices)

if st.button(f"Send Email to {selected_count} Selected Companies", type="primary", disabled=selected_count==0):
    if selected_count == 0:
        st.warning("Please select at least one company from the table above.")
    else:
        progress_bar = st.progress(0)
        for i, index in enumerate(selected_indices):
            row = st.session_state.leads.loc[index]
            
            subj, body = get_email_content(row['Company'], row['Country'])
            
            # Determine Attachment
            if row['Country'] == "France":
                attachment = CV_FILE_FR
            else:
                attachment = CV_FILE_EN
            
            # Send Email
            success = send_email(row['Email'], subj, body, attachment_path=attachment) 
            
            if success:
                st.session_state.leads.at[index, 'Status'] = f"Sent {datetime.now().strftime('%Y-%m-%d')}"
                st.session_state.leads.at[index, 'Select'] = False # Uncheck after sending
                st.toast(f"Sent to {row['Company']}")
            else:
                st.toast(f"Failed to send to {row['Company']}")
            
            progress_bar.progress((i + 1) / selected_count)
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