# =============================================================================
# Lalamove API Configuration Model
# =============================================================================
# This model stores the Lalamove environment configuration (sandbox/production)
# and provides all authentication utilities used by other models in this module.
#
# API credentials (api_key and api_secret) are NOT stored as model fields.
# They are stored securely in Odoo System Parameters (ir.config_parameter)
# under the keys 'lalamove.api_key' and 'lalamove.api_secret'.
# This prevents credentials from being hardcoded or exposed in the UI.
#
# To configure credentials, go to:
# Settings > Technical > Parameters > System Parameters
# =============================================================================

from odoo import models, fields, api
from odoo.exceptions import UserError
import hmac, hashlib, time, uuid, requests

class LalamoveConfig(models.Model):
    _name = 'lalamove.config'
    _description = 'Lalamove API Configuration'

    # Configuration record name (e.g. "FAMTECH Sandbox Config")
    name = fields.Char(required=True)

    # Determines which base URL to use for API calls
    # sandbox = testing environment, production = live environment
    environment = fields.Selection([
        ('sandbox', 'Sandbox'),
        ('production', 'Production')
    ])

    # Auto-computed from environment selection — not editable by user
    base_url = fields.Char(compute='_compute_base_url')

    # Display-only status fields — shows whether credentials are configured
    # in System Parameters without exposing the actual values
    api_key_param = fields.Char(
        string='API Key Status',
        compute='_compute_param_status'
    )
    api_secret_param = fields.Char(
        string='API Secret Status',
        compute='_compute_param_status'
    )

    def _compute_base_url(self):
        """
        Automatically sets the API base URL based on the selected environment.
        - Production: https://rest.lalamove.com
        - Sandbox:    https://rest.sandbox.lalamove.com
        """
        for rec in self:
            if rec.environment == 'production':
                rec.base_url = 'https://rest.lalamove.com'
            else:
                rec.base_url = 'https://rest.sandbox.lalamove.com'

    def _compute_param_status(self):
        """
        Checks whether the API Key and Secret exist in Odoo System Parameters.
        Displays '✓ Configured' or '✗ Not configured' in the form view.
        The actual credential values are never displayed for security.
        """
        for rec in self:
            key = self.env['ir.config_parameter'].sudo().get_param('lalamove.api_key')
            secret = self.env['ir.config_parameter'].sudo().get_param('lalamove.api_secret')
            rec.api_key_param = '✓ Configured' if key else '✗ Not configured'
            rec.api_secret_param = '✓ Configured' if secret else '✗ Not configured'

    def _get_api_key(self):
        """
        Retrieves the Lalamove API Key from Odoo System Parameters at runtime.
        Key is stored under 'lalamove.api_key' in ir.config_parameter.
        """
        return self.env['ir.config_parameter'].sudo().get_param('lalamove.api_key')

    def _get_api_secret(self):
        """
        Retrieves the Lalamove API Secret from Odoo System Parameters at runtime.
        Secret is stored under 'lalamove.api_secret' in ir.config_parameter.
        """
        return self.env['ir.config_parameter'].sudo().get_param('lalamove.api_secret')

    def generate_signature(self, method, path, body=''):
        """
        Generates an HMAC-SHA256 signature for Lalamove API authentication.

        The raw signature string format required by Lalamove:
            {timestamp}\\r\\n{HTTP_METHOD}\\r\\n{path}\\r\\n\\r\\n{body}

        Steps:
        1. Get current Unix timestamp in milliseconds
        2. Build the raw signature string
        3. Hash using HMAC-SHA256 with the API Secret as the key
        4. Return timestamp and lowercase hex signature

        Args:
            method (str): HTTP method e.g. 'GET', 'POST'
            path   (str): API endpoint path e.g. '/v3/orders'
            body   (str): JSON request body as string (empty for GET requests)

        Returns:
            tuple: (timestamp, signature) both as strings
        """
        api_secret = self._get_api_secret()
        if not api_secret:
            raise UserError("Lalamove API Secret is not configured in System Parameters.")

        # Timestamp must be in milliseconds
        timestamp = str(int(time.time() * 1000))

        # Build raw signature string per Lalamove spec
        raw = f'{timestamp}\r\n{method}\r\n{path}\r\n\r\n{body}'

        # Hash with HMAC-SHA256, result must be lowercase hex
        signature = hmac.new(
            api_secret.encode(),
            raw.encode(),
            hashlib.sha256
        ).hexdigest()

        return timestamp, signature

    def get_headers(self, method, path, body=''):
        """
        Builds and returns the complete HTTP headers required for every
        Lalamove API request.

        Required headers per Lalamove API specification:
            Authorization  : hmac {api_key}:{timestamp}:{signature}
            Market         : PH (Philippines market code)
            Request-ID     : UUID v4 unique per request (prevents duplicate processing)
            Content-Type   : application/json (required for POST/PATCH requests)

        Args:
            method (str): HTTP method e.g. 'GET', 'POST'
            path   (str): API endpoint path e.g. '/v3/orders'
            body   (str): JSON request body as string (empty for GET requests)

        Returns:
            dict: Headers dictionary ready to pass to requests.get/post()
        """
        api_key = self._get_api_key()
        if not api_key:
            raise UserError("Lalamove API Key is not configured in System Parameters.")

        timestamp, signature = self.generate_signature(method, path, body)
        token = f'{api_key}:{timestamp}:{signature}'

        return {
            'Authorization': f'hmac {token}',
            'Market': 'PH',
            'Request-ID': str(uuid.uuid4()),  # Unique ID per request
            'Content-Type': 'application/json'
        }

    def test_connection(self):
        """
        Tests the Lalamove API connection by calling GET /v3/cities.
        This endpoint requires valid credentials and returns supported
        cities and service types for the Philippines market.

        Called when the user clicks 'Test Connection' in:
        Settings > Lalamove > Configuration

        Returns:
            dict: Odoo success notification action if connection succeeds

        Raises:
            UserError: If no config found, timeout, connection error,
                       invalid credentials, or unexpected API response
        """
        config = self.search([], limit=1)
        if not config:
            raise UserError("No Lalamove config found. Please create one first.")

        try:
            url = f'{config.base_url}/v3/cities'
            headers = config.get_headers('GET', '/v3/cities')
            response = requests.get(url, headers=headers, timeout=10)

        except requests.exceptions.Timeout:
            raise UserError("Could not reach Lalamove servers. Please try again.")
        except requests.exceptions.ConnectionError:
            raise UserError("Connection error. Please check your internet connection.")

        # Handle API response status codes
        if response.status_code == 200:
            # Connection successful — show green success notification
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Success',
                    'message': 'Lalamove API connection successful!',
                    'type': 'success',
                }
            }
        elif response.status_code == 401:
            # Invalid credentials
            raise UserError("Invalid API credentials. Please check your API Key and Secret.")
        else:
            # Unexpected error — show status code and response for debugging
            raise UserError(f'Connection failed: {response.status_code} - {response.text}')
