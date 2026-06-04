from odoo import models, fields
from odoo.exceptions import UserError
import json, requests, re

class StockPickingExt(models.Model):
    _inherit = 'stock.picking'

    lalamove_order_id = fields.Char(string='Lalamove Order ID')
    lalamove_tracking_url = fields.Char(string='Lalamove Tracking URL')
    lalamove_status = fields.Char(string='Lalamove Delivery Status')
    lalamove_driver_name = fields.Char(string='Driver Name')
    lalamove_driver_phone = fields.Char(string='Driver Phone')

    def _format_phone_e164(self, raw_phone):
        """Convert any PH phone format to E.164 (+639XXXXXXXXX)"""
        phone = re.sub(r'\s+', '', raw_phone)       # remove spaces
        phone = re.sub(r'[^\d+]', '', phone)         # remove non-numeric except +
        if phone.startswith('0'):
            phone = '+63' + phone[1:]                # 09XX → +639XX
        elif phone.startswith('63'):
            phone = '+' + phone                      # 639XX → +639XX
        elif not phone.startswith('+'):
            phone = '+63' + phone                    # add +63 if missing
        return phone

    def action_book_lalamove(self):
        self.ensure_one()
        config = self.env['lalamove.config'].search([], limit=1)
        if not config:
            raise UserError("Please configure Lalamove API credentials in Settings first.")

        # Get linked Sale Order
        sale_order = self.sale_id
        if not sale_order:
            raise UserError("No Sales Order linked to this delivery.")

        # Check quotation ID
        if not sale_order.lalamove_quotation_id:
            raise UserError("No Lalamove quote found. Please get a quote on the Sales Order first.")

        # Check stop IDs
        if not sale_order.lalamove_sender_stop_id or not sale_order.lalamove_recipient_stop_id:
            raise UserError("Stop IDs missing. Please get a fresh Lalamove quote on the Sales Order.")

        # Get & format customer phone
        partner = self.partner_id
        raw_phone = partner.phone or partner.mobile or ''
        if not raw_phone:
            raise UserError("Customer has no phone number. Please add one to the contact.")
        customer_phone = self._format_phone_e164(raw_phone)

        # Get & format company phone for sender
        company = self.env.company
        raw_company_phone = company.phone or ''
        if not raw_company_phone:
            raise UserError("Company has no phone number. Please add one in Settings > Companies.")
        sender_phone = self._format_phone_e164(raw_company_phone)

        body_dict = {
            "data": {
                "quotationId": sale_order.lalamove_quotation_id,
                "sender": {
                    "stopId": sale_order.lalamove_sender_stop_id,
                    "name": company.name,
                    "phone": sender_phone,
                },
                "recipients": [
                    {
                        "stopId": sale_order.lalamove_recipient_stop_id,
                        "name": partner.name,
                        "phone": customer_phone,
                    }
                ],
                "isPODEnabled": False,
            }
        }
        body = json.dumps(body_dict)

        try:
            url = f'{config.base_url}/v3/orders'
            headers = config.get_headers('POST', '/v3/orders', body)
            response = requests.post(url, headers=headers, data=body, timeout=10)
        except requests.exceptions.Timeout:
            raise UserError("Could not reach Lalamove servers. Please try again.")
        except requests.exceptions.ConnectionError:
            raise UserError("Connection error. Please check your internet connection.")

        if response.status_code == 201:
            data = response.json()['data']
            self.write({
                'lalamove_order_id': data.get('orderId', ''),
                'lalamove_status': data.get('status', ''),
                'lalamove_tracking_url': data.get('shareLink', ''),
            })
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'stock.picking',
                'res_id': self.id,
                'view_mode': 'form',
                'target': 'current',
            }
        elif response.status_code == 400:
            raise UserError(f"Lalamove booking failed: {response.json()}")
        elif response.status_code == 401:
            raise UserError("Invalid API credentials.")
        else:
            raise UserError(f"Booking failed: {response.status_code} - {response.text}")

    def sync_all_lalamove_orders(self):
        # Find all pickings with active Lalamove bookings
        active_pickings = self.search([
            ('lalamove_order_id', '!=', False),
            ('lalamove_status', 'not in', [
                'COMPLETED', 'CANCELED', 'REJECTED', 'EXPIRED', False
            ])
        ])

        if not active_pickings:
            return

        config = self.env['lalamove.config'].search([], limit=1)
        if not config:
            return

        for picking in active_pickings:
            order_id = picking.lalamove_order_id
            path = f'/v3/orders/{order_id}'
            url = f'{config.base_url}{path}'
            headers = config.get_headers('GET', path)

            try:
                response = requests.get(url, headers=headers, timeout=10)
            except requests.exceptions.RequestException:
                continue

            if response.status_code == 200:
                data = response.json().get('data', {})
                status = data.get('status', '')

                if status == 'COMPLETED':
                    try:
                        picking.with_context(skip_immediate=True).button_validate()
                    except Exception:
                        pass
                elif status == 'CANCELED':
                    try:
                        picking.action_cancel()
                    except Exception:
                        pass

                picking.write({'lalamove_status': status})
