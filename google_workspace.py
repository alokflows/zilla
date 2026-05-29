import os
from typing import Dict, Any, List, Optional

# Official Google Auth Libraries
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

from workspaces_manager import WorkspaceProvider

# Scopes required for Google Drive and Google Calendar
SCOPES = [
    'https://www.googleapis.com/auth/calendar.readonly',
    'https://www.googleapis.com/auth/drive.readonly',
    'https://www.googleapis.com/auth/drive.file'
]

class GoogleWorkspaceProvider(WorkspaceProvider):
    """
    Google Workspace implementation of the WorkspaceProvider.
    Handles OAuth authentication and API calls to Google Drive and Google Calendar.
    """
    
    def __init__(self, credentials_path: str = 'credentials.json', token_dir: str = 'tokens'):
        """
        Initialize the Google Workspace provider.
        
        Args:
            credentials_path: Path to the OAuth 2.0 Client ID credentials JSON file.
            token_dir: Directory to store user-specific token files.
        """
        self.credentials_path = credentials_path
        self.token_dir = token_dir
        os.makedirs(self.token_dir, exist_ok=True)
        
    def _get_token_path(self, user_id: str) -> str:
        """Get the path to the user's token file for caching."""
        return os.path.join(self.token_dir, f"token_{user_id}.json")

    def authenticate(self, user_id: str, **kwargs) -> bool:
        """
        Authenticate the user via OAuth 2.0.
        Loads existing credentials or initiates the OAuth flow if none exist or they are expired.
        """
        creds = None
        token_path = self._get_token_path(user_id)
        
        # Check if we already have a valid token for this user
        if os.path.exists(token_path):
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
            
        # If there are no valid credentials, let the user log in
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not os.path.exists(self.credentials_path):
                    raise FileNotFoundError(f"OAuth credentials not found at {self.credentials_path}")
                flow = InstalledAppFlow.from_client_secrets_file(self.credentials_path, SCOPES)
                # In a real bot, we'd use flow.authorization_url() and have the user visit it,
                # then capture the callback. For a local stub, run_local_server is used.
                creds = flow.run_local_server(port=0)
                
            # Save the credentials for the next run
            with open(token_path, 'w') as token_file:
                token_file.write(creds.to_json())
                
        return True

    def _get_credentials(self, user_id: str) -> Credentials:
        """Helper method to load credentials for API calls."""
        token_path = self._get_token_path(user_id)
        if not os.path.exists(token_path):
            raise PermissionError(f"User {user_id} is not authenticated.")
        return Credentials.from_authorized_user_file(token_path, SCOPES)

    def get_events(self, user_id: str, start_time: str, end_time: str) -> List[Dict[str, Any]]:
        """
        Retrieve calendar events for the user within the specified timeframe.
        """
        creds = self._get_credentials(user_id)
        service = build('calendar', 'v3', credentials=creds)
        
        # Call the Calendar API
        events_result = service.events().list(
            calendarId='primary', 
            timeMin=start_time,
            timeMax=end_time,
            maxResults=10, 
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        
        # Normalize the events into our standard interface format
        normalized_events = []
        for event in events:
            normalized_events.append({
                'id': event.get('id'),
                'title': event.get('summary'),
                'start': event['start'].get('dateTime', event['start'].get('date')),
                'end': event['end'].get('dateTime', event['end'].get('date')),
                'link': event.get('htmlLink')
            })
            
        return normalized_events

    def get_documents(self, user_id: str, query: str = "") -> List[Dict[str, Any]]:
        """
        Search and retrieve documents from Google Drive.
        """
        creds = self._get_credentials(user_id)
        service = build('drive', 'v3', credentials=creds)
        
        # Construct Drive query to ignore folders and trashed files
        q = "mimeType != 'application/vnd.google-apps.folder' and trashed = false"
        if query:
            q += f" and name contains '{query}'"
            
        results = service.files().list(
            q=q,
            pageSize=10,
            fields="nextPageToken, files(id, name, mimeType, webViewLink)"
        ).execute()
        
        files = results.get('files', [])
        
        # Normalize into standard document format
        normalized_docs = []
        for f in files:
            normalized_docs.append({
                'id': f.get('id'),
                'title': f.get('name'),
                'type': f.get('mimeType'),
                'link': f.get('webViewLink')
            })
            
        return normalized_docs

    def create_document(self, user_id: str, title: str, content: str) -> Dict[str, Any]:
        """
        Create a new Google Doc in Google Drive.
        """
        creds = self._get_credentials(user_id)
        service = build('drive', 'v3', credentials=creds)
        
        file_metadata = {
            'name': title,
            'mimeType': 'application/vnd.google-apps.document'
        }
        
        # As a stub, we are just creating an empty Google Doc with the given title.
        # To add actual text `content`, we would need to use MediaFileUpload 
        # or the Google Docs API directly to insert text into the created document.
        file = service.files().create(
            body=file_metadata, 
            fields='id, name, webViewLink'
        ).execute()
        
        return {
            'id': file.get('id'),
            'title': file.get('name'),
            'link': file.get('webViewLink')
        }
