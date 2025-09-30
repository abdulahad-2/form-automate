from google_auth_oauthlib.flow import Flow
import webbrowser
import json
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# Configuration
CLIENT_SECRETS_FILE = 'credentials.json'
SCOPES = ['https://www.googleapis.com/auth/gmail.send']
REDIRECT_URI = 'http://localhost:8080'  # Must match exactly with credentials.json

class OAuthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        if 'code' in query:
            self.server.auth_code = query['code'][0]
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            success_html = """
                <html>
                    <body>
                        <h1>✅ Authentication Successful!</h1>
                        <p>You can close this window and return to the terminal.</p>
                        <style>
                            body { 
                                font-family: Arial, sans-serif; 
                                text-align: center; 
                                padding: 50px; 
                                background: #f5f5f5;
                            }
                            h1 { color: #2e7d32; }
                        </style>
                    </body>
                </html>
            """.encode('utf-8')
            self.wfile.write(success_html)
        else:
            self.send_response(400)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"Error: No authorization code found in the URL")

def run_local_server():
    server_address = ('', 8080)
    httpd = HTTPServer(server_address, OAuthHandler)
    print("\n🌐 Started local server at http://localhost:8080")
    print("Waiting for OAuth redirect...")
    httpd.handle_request()  # This will block until we get a request
    return httpd.auth_code if hasattr(httpd, 'auth_code') else None

def main():
    try:
        # Create the flow using the web client credentials
        flow = Flow.from_client_secrets_file(
            CLIENT_SECRETS_FILE,
            scopes=SCOPES,
            redirect_uri=REDIRECT_URI
        )
        
        # Generate the authorization URL
        auth_url, _ = flow.authorization_url(
            access_type='offline',
            prompt='consent',
            include_granted_scopes='true'
        )
        
        print("🔑 Starting OAuth flow...")
        print("\n1. Opening your browser to authorize the application...")
        
        try:
            webbrowser.open(auth_url)
        except Exception as e:
            print(f"⚠️  Couldn't open browser automatically: {e}")
            print(f"\nPlease open this URL in your browser:\n{auth_url}")
        
        # Start local server to catch the redirect
        code = run_local_server()
        
        if not code:
            print("\n❌ No authorization code received")
            return
            
        print("\n🔑 Exchanging authorization code for tokens...")
        flow.fetch_token(code=code)
        
        # Save credentials
        creds = flow.credentials
        token_data = {
            'token': creds.token,
            'refresh_token': creds.refresh_token,
            'token_uri': creds.token_uri,
            'client_id': creds.client_id,
            'client_secret': creds.client_secret,
            'scopes': creds.scopes
        }
        
        with open('token.json', 'w') as token_file:
            json.dump(token_data, token_file, indent=2)
        
        print("\n✅ Success! Authentication complete!")
        print("🔒 Token has been saved to 'token.json'")
        print("\nYou can now use this token with your application.")
        
    except Exception as e:
        print(f"\n❌ Error: {str(e)}")
        if hasattr(e, 'error_details'):
            print("Error details:", e.error_details)
        print("\nTroubleshooting:")
        print("1. Make sure your credentials.json is for a 'Web application'")
        print("2. Verify that http://localhost:8080 is in Authorized Redirect URIs")
        print("3. Check that port 8080 is available (no other server running on this port)")

if __name__ == '__main__':
    main()
