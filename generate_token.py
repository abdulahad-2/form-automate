import os
import pickle
import webbrowser
from google_auth_oauthlib.flow import Flow

# Define the scopes
SCOPES = ['https://www.googleapis.com/auth/gmail.send']

def main():
    # Check if credentials.json exists
    if not os.path.exists('credentials.json'):
        print("❌ Error: credentials.json not found")
        print("\nPlease follow these steps:")
        print("1. Go to https://console.cloud.google.com/")
        print("2. Create a project and enable Gmail API")
        print("3. Create OAuth 2.0 credentials (Web application)")
        print("4. Set Authorized redirect URIs to: http://localhost:8080/")
        print("5. Download the credentials as 'credentials.json' and place it here")
        return

    print("✅ Found credentials.json")
    print("\n🔑 Starting OAuth flow...")
    
    # Create the flow using the client secrets file
    flow = Flow.from_client_secrets_file(
        'credentials.json',
        scopes=SCOPES,
        redirect_uri='http://localhost:8080/'
    )
    
    # Generate the authorization URL
    auth_url, _ = flow.authorization_url(access_type='offline', prompt='consent')
    
    print("\n🔗 Opening authorization URL in your browser...")
    try:
        webbrowser.open(auth_url)
        print("✅ Opened the authorization URL in your default browser.")
    except Exception as e:
        print(f"⚠️  Could not open browser automatically: {e}")
        print("\nPlease open this URL manually in your browser:")
    
    print("\n" + "="*80)
    print(auth_url)
    print("="*80 + "\n")
    
    print("Please follow these steps:")
    print("1. Sign in with your Google account (if not already signed in)")
    print("2. Click 'Allow' to grant the required permissions")
    print("3. You'll be redirected to a localhost page that won't load (this is expected)")
    print("4. Copy the ENTIRE URL from your browser's address bar")
    
    # Get the authorization response
    auth_response = input("\nPaste the ENTIRE URL you were redirected to and press Enter: ")
    
    try:
        # Exchange the authorization code for credentials
        flow.fetch_token(authorization_response=auth_response)
        credentials = flow.credentials
        
        # Save the credentials
        with open('token.pickle', 'wb') as token:
            pickle.dump(credentials, token)
        
        print("\n✅ Success! Your token has been saved to token.pickle")
        print("You can now run your application with Gmail API access!")
        
    except Exception as e:
        print(f"\n❌ Error: {str(e)}")
        if "invalid_grant" in str(e):
            print("The authorization code might be invalid or expired. Please try again.")
        else:
            print("Please make sure you copied the ENTIRE URL correctly and try again.")

if __name__ == '__main__':
    main()
