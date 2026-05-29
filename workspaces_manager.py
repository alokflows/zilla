from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional

class WorkspaceProvider(ABC):
    """
    Abstract base class representing a Workspace Provider (e.g., Google Workspace, Microsoft 365, Notion).
    Defines the contract that all workspace integrations must implement.
    """
    
    @abstractmethod
    def authenticate(self, user_id: str, **kwargs) -> bool:
        """
        Authenticate the user with the workspace provider.
        
        Args:
            user_id: A unique identifier for the user.
            kwargs: Additional arguments for authentication (e.g., tokens, codes).
            
        Returns:
            bool: True if authentication was successful, False otherwise.
        """
        pass
        
    @abstractmethod
    def get_events(self, user_id: str, start_time: str, end_time: str) -> List[Dict[str, Any]]:
        """
        Retrieve calendar events for the user within the specified timeframe.
        
        Args:
            user_id: A unique identifier for the user.
            start_time: ISO formatted start time string.
            end_time: ISO formatted end time string.
            
        Returns:
            List[Dict]: A list of normalized event dictionaries.
        """
        pass
        
    @abstractmethod
    def get_documents(self, user_id: str, query: str = "") -> List[Dict[str, Any]]:
        """
        Search and retrieve documents from the workspace's drive/storage.
        
        Args:
            user_id: A unique identifier for the user.
            query: Optional search query to filter documents.
            
        Returns:
            List[Dict]: A list of normalized document dictionaries.
        """
        pass
        
    @abstractmethod
    def create_document(self, user_id: str, title: str, content: str) -> Dict[str, Any]:
        """
        Create a new document in the workspace.
        
        Args:
            user_id: A unique identifier for the user.
            title: Title of the document.
            content: Text content of the document.
            
        Returns:
            Dict: Normalized dictionary representing the created document.
        """
        pass

class WorkspaceManager:
    """
    Manager class to handle multiple workspace integrations in a modular fashion.
    """
    
    def __init__(self):
        self._providers: Dict[str, WorkspaceProvider] = {}
        
    def register_provider(self, name: str, provider: WorkspaceProvider) -> None:
        """
        Register a new workspace provider.
        
        Args:
            name: The name/identifier of the provider (e.g., 'google', 'microsoft').
            provider: An instance of a class that inherits from WorkspaceProvider.
        """
        self._providers[name] = provider
        
    def get_provider(self, name: str) -> Optional[WorkspaceProvider]:
        """
        Retrieve a registered workspace provider by name.
        
        Args:
            name: The name/identifier of the provider.
            
        Returns:
            WorkspaceProvider or None if not found.
        """
        return self._providers.get(name)
        
    def authenticate_user(self, provider_name: str, user_id: str, **kwargs) -> bool:
        """
        Authenticate a user for a specific provider.
        """
        provider = self.get_provider(provider_name)
        if not provider:
            raise ValueError(f"Provider '{provider_name}' is not registered.")
        return provider.authenticate(user_id, **kwargs)
