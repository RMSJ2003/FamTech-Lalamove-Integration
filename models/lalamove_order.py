from odoo import models, fields, api
from odoo.exceptions import UserError
import json
import requests

class StockPicking(models.Model):
    _inherit = 'stock.picking'

    ### Step 3C: Book via Lalamove
    def action_book_lalamove(self):

        # 1. Prevent duplicate bookings
        if self.lalamove_order_id:
            raise UserError("This delivery has already been booked with Lalamove.")

        # 2. Get Lalamove config
        config = self.env['lalamove.config'].search([], limit=1)
        if not config:
            raise UserError("Please configure Lalamove API in Settings first.")

        # 3. Validate recipient phone
        partner = self.partner_id
        phone = partner.phone or partner.mobile
        if not phone:
            raise UserError("Please add a phone number to the customer contact.")

        # 4. Validate recipient address
        if not partner.street or not partner.city:
            raise UserError("Delivery address is incomplete. Please add street and city.")

        # 5. Get quotation ID from related sale order
        quotation_id = None
        if self.sale_id and self.sale_id.lalamove_quotation_id:
            quotation_id = self.sale_id.lalamove_quotation_id
        
        if not quotation_id:
            raise UserError(
                "No Lalamove quotation found. "
                "Please get a quote from the Sales Order first."
            )

        # 6. Get sender details from company
        company = self.env.company
        sender_phone = company.phone or ''
        sender_name = company.name or 'FAMTECH'

        # 7. Build order payload
        # Note: stopId values come from the quotation stops
        # For now we reference the sale order's quotation stops
        sale = self.sale_id
        payload = {
            "data": {
                "quotationId": quotation_id,
                "sender": {
                    "name": sender_name,
                    "phone": sender_phone
                },
                "recipients": [
                    {
                        "name": partner.name,
                        "phone": phone,
                        "remarks": f"Delivery for {self.origin or self.name}"
                    }
                ]
            }
        }

        # 8. Call Lalamove POST /v3/orders
        url = f'{config.base_url}/v3/orders'
        body = json.dumps(payload)
        headers = config.get_headers('POST', '/v3/orders', body)

        try:
            response = requests.post(url, headers=headers, data=body, timeout=10)
        except requests.exceptions.Timeout:
            raise UserError("Could not reach Lalamove servers. Please try again.")
        except requests.exceptions.RequestException as e:
            raise UserError(f"Connection error: {str(e)}")

        # 9. Handle response
        if response.status_code == 201:
            data = response.json().get('data', {})
            self.write({
                'lalamove_order_id': data.get('orderId', ''),
                'lalamove_tracking_url': data.get('shareLink', ''),
                'lalamove_status': data.get('status', ''),
            })
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Booking Confirmed',
                    'message': f"Lalamove order booked! Order ID: {data.get('orderId')}",
                    'type': 'success',
                }
            }
        else:
            error_msg = response.json() if response.content else response.text
            raise UserError(f"Lalamove booking failed: {error_msg}")

    ### Step 3D: Sync all active Lalamove orders
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
                # Skip this picking and continue with others
                continue

            if response.status_code == 200:
                data = response.json().get('data', {})
                status = data.get('status', '')
                
                vals = {'lalamove_status': status}

                # Map status to Odoo actions
                if status == 'COMPLETED':
                    picking.with_context(skip_immediate=True).button_validate()
                elif status == 'CANCELED':
                    picking.action_cancel()

                picking.write(vals)
