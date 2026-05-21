from odoo import models, fields, api
from odoo.exceptions import UserError
import hmac, hashlib, time, requests

class LalamoveConfig(models.Model):
    _name = 'lalamove.config'
    _description = 'Lalamove API Configuration'

    name = fields.Char(required=True)
    environment = fields.Selection([
        ('sandbox', 'Sandbox'),
        ('production', 'Production')
    ])
    base_url = fields.Char(compute='_compute_base_url')
    api_key_param = fields.Char(
        string='API Key Status',
        compute='_compute_param_status'
    )
    api_secret_param = fields.Char(
        string='API Secret Status',
        compute='_compute_param_status'
    )

    def _compute_base_url(self):
        for rec in self:
            if rec.environment == 'production':
                rec.base_url = 'https://rest.lalamove.com'
            else:
                rec.base_url = 'https://rest.sandbox.lalamove.com'


    def _compute_param_status(self):
        for rec in self:
            key = self.env['ir.config_parameter'].sudo().get_param('lalamove.api_key')
            secret = self.env['ir.config_parameter'].sudo().get_param('lalamove.api_secret')
            rec.api_key_param = '✓ Configured' if key else '✗ Not configured'
            rec.api_secret_param = '✓ Configured' if secret else '✗ Not configured'

    def _get_api_key(self):
        return self.env['ir.config_parameter'].sudo().get_param('lalamove.api_key')

    def _get_api_secret(self):
        return self.env['ir.config_parameter'].sudo().get_param('lalamove.api_secret')

    def generate_signature(self, method, path, body=''):
        api_secret = self._get_api_secret()
        if not api_secret:
            raise UserError("Lalamove API Secret is not configured in System Parameters.")
        timestamp = str(int(time.time() * 1000))
        raw = f'{timestamp}\r\n{method}\r\n{path}\r\n\r\n{body}'
        signature = hmac.new(
            api_secret.encode(),
            raw.encode(),
            hashlib.sha256
        ).hexdigest()
        return timestamp, signature

    def get_headers(self, method, path, body=''):
        api_key = self._get_api_key()
        if not api_key:
            raise UserError("Lalamove API Key is not configured in System Parameters.")
        timestamp, signature = self.generate_signature(method, path, body)
        token = f'{api_key}:{timestamp}:{signature}'
        return {
            'Authorization': f'hmac {token}',
            'Market': 'PH',
            'Request-ID': str(time.time()),
            'Content-Type': 'application/json'
        }

    def test_connection(self):
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

        if response.status_code == 200:
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
            raise UserError("Invalid API credentials. Please check your API Key and Secret.")
        else:
            raise UserError(f'Connection failed: {response.status_code} - {response.text}')