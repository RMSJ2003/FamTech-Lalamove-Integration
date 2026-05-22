from odoo import models, fields

class StockPickingExt(models.Model):
    _inherit = 'stock.picking'

    lalamove_order_id = fields.Char(string='Lalamove Order ID')
    lalamove_tracking_url = fields.Char(string='Tracking URL')
    lalamove_status = fields.Char(string='Delivery Status')
    lalamove_driver_name = fields.Char(string='Driver Name')
    lalamove_driver_phone = fields.Char(string='Driver Phone')

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
            # Skip this picking, try again next cycle
            continue

        if response.status_code == 200:
            data = response.json().get('data', {})
            status = data.get('status', '')

            # Map status to Odoo actions
            if status == 'COMPLETED':
                try:
                    picking.with_context(
                        skip_immediate=True
                    ).button_validate()
                except Exception:
                    pass
            elif status == 'CANCELED':
                try:
                    picking.action_cancel()
                except Exception:
                    pass

            picking.write({'lalamove_status': status})
