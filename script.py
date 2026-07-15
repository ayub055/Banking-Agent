import requests
from typing import Optional
from datetime import datetime, timedelta
import os


class KotakOAuth2Handler:
    """Handler for OAuth2 token generation from Kotak IDAM."""

    def __init__(
        self,
        token_url: str = "https://uat.api.idam.kotak.internal/oauth2/token",
        client_id: str = "*********",
        client_secret: str = "**********",
        ca_bundle: str = "Path to Certificates.pem",
    ):
        """
        Initialize the OAuth2 handler.

        Args:
            token_url: The OAuth2 token endpoint URL
            client_id: OAuth2 client ID
            client_secret: OAuth2 client secret
            ca_bundle: Path to CA certificate bundle (e.g., ~/kotak-ca.pem)
        """
        self.token_url = token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.ca_bundle = os.path.expanduser(ca_bundle) if ca_bundle else None
        self.token = None
        self.token_expires_at = None

    def generate_token(self) -> str:
        """
        Generate a new OAuth2 bearer token.

        Args:
            client_id: OAuth2 client ID
            client_secret: OAuth2 client secret

        Returns:
            Bearer token string

        Raises:
            requests.exceptions.RequestException: If the token request fails
        """
        if not self.client_id or not self.client_secret:
            raise ValueError("client_id and client_secret are required")

        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }

        try:
            response = requests.post(
                self.token_url, headers=headers, data=data, timeout=30, verify=self.ca_bundle or True
            )
            response.raise_for_status()
            token_response = response.json()
            self.token = token_response.get("access_token")
            expires_in = token_response.get("expires_in", 3600)
            self.token_expires_at = datetime.now() + timedelta(seconds=expires_in)
            return self.token
        except requests.exceptions.RequestException as e:
            print(f"OAuth2 token request failed: {e}")
            raise

    def is_token_expired(self) -> bool:
        """Check if the current token is expired."""
        if not self.token or not self.token_expires_at:
            return True
        return datetime.now() >= self.token_expires_at

    def get_valid_token(self) -> str:
        """
        Get a valid token, refreshing if necessary.

        Returns:
            Valid bearer token string
        """
        if self.is_token_expired():
            self.generate_token()
        return self.token


class KotakAIWrapper:
    """Wrapper class for Kotak AI API requests to the Anthropic chat completions endpoint."""

    def __init__(
        self,
        api_url: str = "https://dev.ai.kotak.internal/model/anthropic/api/v1/chat/completions",
        bearer_token: str = None,
        oauth2_handler: KotakOAuth2Handler = None,
        ca_bundle: str = "/Users/KMBL404318/Documents/Certificates/Certificates.pem",
    ):
        """
        Initialize the KotakAIWrapper.

        Args:
            api_url: The API endpoint URL
            bearer_token: Authentication bearer token (static)
            oauth2_handler: KotakOAuth2Handler instance for dynamic token generation
            ca_bundle: Path to CA certificate bundle (e.g., ~/kotak-ca.pem)
        """
        self.api_url = api_url
        self.bearer_token = bearer_token
        self.oauth2_handler = oauth2_handler
        self.ca_bundle = os.path.expanduser(ca_bundle) if ca_bundle else None
        self.headers = {
            "Content-Type": "application/json",
        }
        if bearer_token: self.headers["Authorization"] = f"Bearer {bearer_token}"

    def send_message(
        self,
        message: str,
        model: str = "sonnet3.5",
        max_tokens: int = 150,
        temperature: float = 0.7,
        stream: bool = False,
        system: str = None,
    ) -> dict:
        """
        Send a message to the Kotak AI API and get a response.

        Args:
            message: The user message content
            model: The model name to use (default: "sonnet3.5")
            max_tokens: Maximum tokens in the response (default: 150)
            temperature: Temperature for response generation (default: 0.7)
            stream: Whether to stream the response (default: False)
            system: System prompt to guide the model behavior

        Returns:
            Dictionary containing the API response

        Raises:
            requests.exceptions.RequestException: If the API request fails
            ValueError: If no valid bearer token is available
        """
        headers = self.headers.copy()

        if self.oauth2_handler:
            token = self.oauth2_handler.get_valid_token()
            headers["Authorization"] = f"Bearer {token}"
        elif not headers.get("Authorization"):
            raise ValueError("No bearer token available. Provide token or OAuth2 handler.")

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": message}],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
        }

        if system:
            payload["system"] = system

        try:
            response = requests.post(
                self.api_url, json=payload, headers=headers, timeout=30, verify=self.ca_bundle or True
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"API request failed: {e}")
            if hasattr(e.response, 'text'):
                print(f"Response body: {e.response.text[:500]}")
            raise

    def set_bearer_token(self, bearer_token: str) -> None:
        """
        Update the bearer token for authentication.

        Args:
            bearer_token: New authentication bearer token
        """
        self.bearer_token = bearer_token
        self.headers["Authorization"] = f"Bearer {bearer_token}"




if __name__ == "__main__":
    pass

 