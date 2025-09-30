from google_auth_oauthlib.flow import InstalledAppFlow

# Define the scopes
SCOPES = ['https://www.googleapis.com/auth/gmail.send']

# Create the flow
flow = InstalledAppFlow.from_client_secrets_file(
    'credentials.json',
    scopes=SCOPES,
    redirect_uri='http://localhost:8080'
)

# Generate the authorization URL
auth_url, _ = flow.authorization_url(prompt='consent')

print("\n" + "="*80)
print("🔗 AUTHORIZATION URL (COPY AND PASTE IN BROWSER):")
print("="*80)
print(auth_url)
print("="*80)
print("\nAfter authorizing, you'll get a code to paste back here.\n")
