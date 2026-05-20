from odoo import models, fields
import hmac, hashlib, time, json, requests

class LalamoveConfig(models.Model):
    _name = 'lalamove.config'
    _description = 'Lalamove API Configuration'
    
    name = fields.Char(required=True)
    api_key = fields.Char(string='API Key', required=True)
    api_secret = fields.Char(string='API Secret', required=True)
    environment = fields.Selection([('sandbox','Sandbox'),('production','Production')])
    base_url = fields.Char(compute='_compute_base_url')
    
    def _compute_base_url(self):
        for rec in self:
            if rec.environment == 'production':
                rec.base_url = 'https://rest.lalamove.com'
            else:
                rec.base_url = 'https://sandbox-rest.lalamove.com'

    def generate_signature(self, method, path, body=''):
        timestamp = str(int(time.time() * 1000))
        raw = f'{timestamp}\r\n{method}\r\n{path}\r\n\r\n{body}'
        signature = hmac.new(
            self.api_secret.encode(),
            raw.encode(),
            hashlib.sha256
        ).hexdigest()
        return timestamp, signature

    def get_headers(self, method, path, body=''):
        timestamp, signature = self.generate_signature(method, path, body)
        token = f'{self.api_key}:{timestamp}:{signature}'
        return {
            'Authorization': f'hmac {token}',
            'Market': 'PH',
            'Request-ID': str(time.time()),
            'Content-Type': 'application/json'
        }

    def test_connection(self):
        config = self.search([], limit=1)
        if not config:
            raise Exception("No Lalamove config found. Please create one first.")
        
        url = f'{config.base_url}/v3/cities'
        headers = config.get_headers('GET', '/v3/cities')
        
        response = requests.get(url, headers=headers)
        
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
        else:
            raise Exception(f'Connection failed: {response.status_code} - {response.text}')