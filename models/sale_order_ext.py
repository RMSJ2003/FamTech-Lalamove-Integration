from odoo import models, fields, api
from odoo.exceptions import UserError
import json, requests


class SaleOrderExt(models.Model):
    _inherit = 'sale.order'

    lalamove_fee = fields.Float(string="Lalamove Fee")
    lalamove_quote_fee = fields.Char(string='Lalamove Quote Fee')
    lalamove_quote_eta = fields.Char(string='Estimated Delivery Time')
    lalamove_quotation_id = fields.Char(string='Quotation ID')
    lalamove_tracking_url = fields.Char(string='Tracking URL', readonly=True)
    lalamove_order_id = fields.Char(string='Lalamove Order ID', readonly=True)
    lalamove_status = fields.Char(string='Lalamove Delivery Status', readonly=True)
    lalamove_sender_stop_id = fields.Char(string='Sender Stop ID', readonly=True)
    lalamove_recipient_stop_id = fields.Char(string='Recipient Stop ID', readonly=True)

    def action_get_lalamove_quote(self):
        config = self.env['lalamove.config'].search([], limit=1)
        if not config:
            raise UserError("Please configure Lalamove API credentials in Settings first.")

        company = self.env.company
        if not company.street or not company.city:
            raise UserError("Please complete your company address in Settings before requesting a quote.")

        partner = self.partner_id
        if not partner.street or not partner.city:
            raise UserError("Please add a complete delivery address to the customer contact.")

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

        try:
            url = f'{config.base_url}/v3/quotations'
            headers = config.get_headers('POST', '/v3/quotations', body)
            response = requests.post(url, headers=headers, data=body, timeout=10)
        except requests.exceptions.Timeout:
            raise UserError("Could not reach Lalamove servers. Please try again.")
        except requests.exceptions.ConnectionError:
            raise UserError("Connection error. Please check your internet connection.")

        if response.status_code == 201:
            data = response.json()['data']

            stops = data.get('stops', [])
            sender_stop_id = stops[0].get('stopId', '') if len(stops) > 0 else ''
            recipient_stop_id = stops[1].get('stopId', '') if len(stops) > 1 else ''

            fee = float(data.get('priceBreakdown', {}).get('total', 0))

            if self.lalamove_fee:
                raise UserError("Lalamove fee already applied. Refresh or reset before re-quoting.")

            self.env['lalamove.quotation'].create({
                'sale_order_id': self.id,
                'total_fee': fee,
                'currency': data['priceBreakdown']['currency'],
                'service_type': data['serviceType'],
                'quotation_id': data['quotationId'],
                'expires_at': data['expiresAt'],
            })

            self.lalamove_quote_fee = f"{fee} {data['priceBreakdown']['currency']}"
            self.lalamove_quotation_id = data['quotationId']
            self.lalamove_fee = fee
            self.lalamove_quote_eta = data.get('expiresAt', '')
            self.lalamove_sender_stop_id = sender_stop_id
            self.lalamove_recipient_stop_id = recipient_stop_id

            # Distribute Lalamove fee per line
            # Distribute Lalamove fee per line
            if self.order_line:
                fee_per_line = fee / len(self.order_line)
                for line in self.order_line:
                    has_vat = any(
                        abs(t.amount - 12.0) < 0.01
                        for t in line.tax_id
                        if t.amount_type == 'percent'
                    )
                    if has_vat:
                        line.lalamove_cost = fee_per_line
                    else:
                        line.lalamove_cost = fee_per_line / 1.12

                    # ── Force-write margin directly so native total picks it up ──
                    revenue = line.price_subtotal or 0.0
                    unit_cost = line.purchase_price or line.product_id.sudo().standard_price or 0.0
                    cost = unit_cost * (line.product_uom_qty or 0.0)
                    new_margin = revenue - cost - line.lalamove_cost

                    # Write directly, bypassing compute
                    line.write({
                        'margin': new_margin,
                        'margin_percent': (new_margin / revenue * 100) if revenue else 0.0,
                    })

            return {
                'type': 'ir.actions.client',
                'tag': 'reload',
            }

        elif response.status_code == 401:
            raise UserError("Invalid API credentials.")
        elif response.status_code == 422:
            raise UserError("Invalid address.")
        else:
            raise UserError(f"Failed to get quote: {response.status_code} - {response.text}")


class SaleOrderLineExt(models.Model):
    _inherit = 'sale.order.line'

    purchase_price = fields.Float(string="Cost")
    lalamove_cost = fields.Float(string="Lalamove Fee")

    # Redeclare margin fields pointing to OUR compute method
    margin = fields.Monetary(
        string="Margin",
        compute='_compute_margin_lalamove',
        store=True,
        currency_field='currency_id',
    )
    margin_percent = fields.Float(
        string="Margin (%)",
        compute='_compute_margin_lalamove',
        store=True,
    )

    @api.depends('price_subtotal', 'purchase_price', 'product_uom_qty', 'lalamove_cost', 'product_id')
    def _compute_margin_lalamove(self):
        for line in self:
            # price_subtotal is always VAT-exclusive in Odoo
            revenue = line.price_subtotal or 0.0
            unit_cost = line.purchase_price or line.product_id.sudo().standard_price or 0.0
            cost = unit_cost * (line.product_uom_qty or 0.0)
            lalamove = line.lalamove_cost or 0.0

            line.margin = revenue - cost - lalamove

            if revenue:
                line.margin_percent = (line.margin / revenue) * 100
            else:
                line.margin_percent = 0.0
