import os
import msal
import requests

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class GraphClient:
    def __init__(self, tenant_id=None, client_id=None, client_secret=None):
        self.tenant_id = tenant_id or os.environ["GRAPH_TENANT_ID"]
        self.client_id = client_id or os.environ["GRAPH_CLIENT_ID"]
        self.client_secret = client_secret or os.environ["GRAPH_CLIENT_SECRET"]
        self._app = msal.ConfidentialClientApplication(
            self.client_id,
            authority=f"https://login.microsoftonline.com/{self.tenant_id}",
            client_credential=self.client_secret,
        )

    def _token(self):
        result = self._app.acquire_token_silent(
            ["https://graph.microsoft.com/.default"], account=None
        )
        if not result:
            result = self._app.acquire_token_for_client(
                scopes=["https://graph.microsoft.com/.default"]
            )
        if "access_token" not in result:
            raise RuntimeError(
                f"Failed to acquire Graph token: {result.get('error_description', result)}"
            )
        return result["access_token"]

    def _headers(self):
        return {
            "Authorization": f"Bearer {self._token()}",
            "Content-Type": "application/json",
        }

    def get(self, path, params=None):
        resp = requests.get(f"{GRAPH_BASE}{path}", headers=self._headers(), params=params)
        resp.raise_for_status()
        return resp.json()

    def patch(self, path, body):
        resp = requests.patch(f"{GRAPH_BASE}{path}", headers=self._headers(), json=body)
        resp.raise_for_status()
        return resp

    def post(self, path, body=None):
        resp = requests.post(f"{GRAPH_BASE}{path}", headers=self._headers(), json=body or {})
        resp.raise_for_status()
        return resp

    def delete(self, path, ignore_404=False):
        resp = requests.delete(f"{GRAPH_BASE}{path}", headers=self._headers())
        if ignore_404 and resp.status_code == 404:
            return resp
        resp.raise_for_status()
        return resp
