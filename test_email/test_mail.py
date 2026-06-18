import os, sys, smtplib, ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
from pathlib import Path

# Load .env from same folder as this script
load_dotenv(dotenv_path=Path(__file__).parent / '.env')

TO = sys.argv[1] if len(sys.argv) > 1 else None

SERVER   = os.getenv('MAIL_SERVER',   'smtp.gmail.com').strip()
PORT_STR = os.getenv('MAIL_PORT',     '587').strip()
USERNAME = os.getenv('MAIL_USERNAME', '').strip()
PASSWORD = os.getenv('MAIL_PASSWORD', '').strip()
FROM     = os.getenv('MAIL_FROM',     USERNAME).strip() or USERNAME

print("\n── Email config ─────────────────────────────")
print(f"  MAIL_SERVER:   {SERVER!r}")
print(f"  MAIL_PORT:     {PORT_STR!r}")
print(f"  MAIL_USERNAME: {USERNAME!r}")
print(f"  MAIL_PASSWORD: {'*' * len(PASSWORD) if PASSWORD else '(NOT SET)'}")
print(f"  MAIL_FROM:     {FROM!r}")
print(f"  Sending to:    {TO!r}")
print("─────────────────────────────────────────────\n")

if not USERNAME:
    print("✗ MAIL_USERNAME is not set in your .env — add it and try again.")
    print("  Example:  MAIL_USERNAME=yourgmail@gmail.com")
    sys.exit(1)

if not PASSWORD:
    print("✗ MAIL_PASSWORD is not set in your .env — add it and try again.")
    print("  For Gmail: go to https://myaccount.google.com/apppasswords")
    print("  Create an App Password for Mail and paste it as MAIL_PASSWORD.")
    sys.exit(1)

if not TO:
    print("✗ Provide a recipient:  python test_email_setup.py recipient@email.com")
    sys.exit(1)

# Build test message
msg = MIMEMultipart('alternative')
msg['Subject'] = 'VaultCore email test'
msg['From']    = f'VaultCore Wallet <{FROM}>'
msg['To']      = TO
msg.attach(MIMEText('This is a test email from VaultCore reset setup.', 'plain'))

def try_send(use_ssl: bool):
    port = 465 if use_ssl else 587
    mode = 'SSL port 465' if use_ssl else 'STARTTLS port 587'
    print(f"  Trying {mode}…", end=' ', flush=True)
    try:
        if use_ssl:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(SERVER, 465, context=ctx, timeout=15) as s:
                s.ehlo(); s.login(USERNAME, PASSWORD)
                s.sendmail(FROM, [TO], msg.as_string())
        else:
            with smtplib.SMTP(SERVER, 587, timeout=15) as s:
                s.ehlo(); s.starttls(); s.ehlo(); s.login(USERNAME, PASSWORD)
                s.sendmail(FROM, [TO], msg.as_string())
        print(f"✓ SENT successfully via {mode}")
        return True
    except smtplib.SMTPAuthenticationError as e:
        print(f"✗ AUTH FAILED")
        print(f"\n  Error: {e}")
        print( "\n  Fix:")
        print( "    • Go to https://myaccount.google.com/apppasswords")
        print( "    • 2-Step Verification must be ON first")
        print( "    • Create App Password → Mail → Other → name it VaultCore")
        print( "    • Paste the 16-char code as MAIL_PASSWORD in .env")
        print( "    • Do NOT use your normal Google password\n")
        return False
    except smtplib.SMTPConnectError as e:
        print(f"✗ CONNECT FAILED: {e}")
        return False
    except smtplib.SMTPException as e:
        print(f"✗ SMTP ERROR: {e}")
        return False
    except OSError as e:
        print(f"✗ NETWORK ERROR: {e}")
        return False

print("Testing connection…")
ok = try_send(use_ssl=False) or try_send(use_ssl=True)

if ok:
    print(f"\n✓ Success! Check {TO!r} inbox (and spam folder).")
    print("  Your .env email config is correct.\n")
else:
    print("\n✗ Both ports failed. Common fixes:")
    print("  1. Use a Gmail App Password (not your account password)")
    print("  2. Enable 2-Step Verification on the Gmail account first")
    print("  3. Check MAIL_SERVER is correct for your provider")
    print("  4. Make sure your network allows outbound SMTP\n")
    sys.exit(1)
