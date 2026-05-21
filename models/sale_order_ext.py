from odoo import models, fields
from odoo.exceptions import UserError
import json, requests

class SaleOrderExt(models.Model):
    _inherit = 'sale.order'

    lalamove_quote_fee = fields.Char(string='Lalamove Quote Fee')
    lalamove_quote_eta = fields.Char(string='Estimated Delivery Time')
    lalamove_quotation_id = fields.Char(string='Quotation ID')
    lalamove_tracking_url = fields.Char(
        string='Tracking URL',
        related='picking_ids.lalamove_tracking_url',
        readonly=True
    )
    lalamove_order_id = fields.Char(
        string='Lalamove Order ID',
        related='picking_ids.lalamove_order_id',
        readonly=True
    )
    lalamove_status = fields.Char(
        string='Delivery Status',
        related='picking_ids.lalamove_status',
        readonly=True
    )

    def action_get_lalamove_quote(self):
        # Check config
        config = self.env['lalamove.config'].search([], limit=1)
        if not config:
            raise UserError("Please configure Lalamove API credentials in Settings first.")

        # Validate company address
        company = self.env.company
        if not company.street or not company.city:
            raise UserError("Please complete your company address in Settings before requesting a quote.")

        # Validate customer address
        partner = self.partner_id
        if not partner.street or not partner.city:
            raise UserError("Please add a complete delivery address to the customer contact.")

        # Validate customer phone
        if not partner.phone and not partner.mobile:
            raise UserError("Please add a phone number to the customer contact before requesting a quote.")

        pickup_address = f"{company.street}, {company.city}"
        delivery_address = f"{partner.street}, {partner.city}"

        body_dict = {
            "data": {
                "serviceType": "MOTORCYCLE",
                "language": "en_PH",
                "stops": [
                    {
                        "coordinates": {"lat": "14.5995", "lng": "120.9842"},
                        "address": pickup_address
                    },
                    {
                        "coordinates": {"lat": "14.5547", "lng": "121.0244"},
                        "address": delivery_address
                    }
                ]
            }
        }
        body = json.dumps(body_dict)

        # Call API with timeout handling
        try:
            url = f'{config.base_url}/v3/quotations'
            headers = config.get_headers('POST', '/v3/quotations', body)
            response = requests.post(url, headers=headers, data=body, timeout=10)
        except requests.exceptions.Timeout:
            raise UserError("Could not reach Lalamove servers. Please try again.")
        except requests.exceptions.ConnectionError:
            raise UserError("Connection error. Please check your internet connection and try again.")

        if response.status_code == 201:
            data = response.json()['data']

            self.env['lalamove.quotation'].create({
                'sale_order_id': self.id,
                'total_fee': data['priceBreakdown']['total'],
                'currency': data['priceBreakdown']['currency'],
                'service_type': data['serviceType'],
                'quotation_id': data['quotationId'],
                'expires_at': data['expiresAt'],
            })

            self.lalamove_quote_fee = f"{data['priceBreakdown']['total']} {data['priceBreakdown']['currency']}"
            self.lalamove_quotation_id = data['quotationId']

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Quote Retrieved!',
                    'message': f"Delivery fee: {data['priceBreakdown']['total']} {data['priceBreakdown']['currency']}",
                    'type': 'success',
                }
            }
        elif response.status_code == 401:
            raise UserError("Invalid API credentials. Please check your Lalamove API Key and Secret in Settings.")
        elif response.status_code == 422:
            raise UserError("Invalid address. The delivery address is not supported by Lalamove.")
        else:
            raise UserError(f"Failed to get quote: {response.status_code} - {response.text}")