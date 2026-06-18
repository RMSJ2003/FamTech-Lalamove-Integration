# =============================================================================
# Sale Order Extension — Lalamove Integration
# =============================================================================
# Extends sale.order and sale.order.line with Lalamove-specific fields
# and the quotation retrieval logic.
#
# Key additions:
# 1. Lalamove fields on sale.order (quotation ID, stop IDs, status, etc.)
# 2. action_get_lalamove_quote() — calls POST /v3/quotations and saves results
# 3. Lalamove fee distribution across order lines
# 4. Margin recalculation per line factoring in the Lalamove delivery cost
#
# Note: stopId values returned by the quotation response are saved on the
# sale.order record and passed to POST /v3/orders during booking in
# stock_picking_ext.py
# =============================================================================

from odoo import models, fields, api
from odoo.exceptions import UserError
import json, requests


class SaleOrderExt(models.Model):
    _inherit = 'sale.order'

    # Numeric delivery fee — used for margin calculations and fee distribution
    lalamove_fee = fields.Float(string="Lalamove Fee")

    # Human-readable fee display (e.g. "90 PHP") — shown in the form view
    lalamove_quote_fee = fields.Char(string='Lalamove Quote Fee')

    # Expiration timestamp of the quotation — valid for 5 minutes only
    lalamove_quote_eta = fields.Char(string='Estimated Delivery Time')

    # Unique quotation ID returned by POST /v3/quotations
    # Required as input when calling POST /v3/orders to book a delivery
    lalamove_quotation_id = fields.Char(string='Quotation ID')

    # Tracking URL (shareLink) pulled from the linked stock.picking record
    # Populated after booking via stock_picking_ext.py
    lalamove_tracking_url = fields.Char(string='Tracking URL', readonly=True)

    # Lalamove order ID pulled from the linked stock.picking record
    # Populated after booking via stock_picking_ext.py
    lalamove_order_id = fields.Char(string='Lalamove Order ID', readonly=True)

    # Current delivery status pulled from the linked stock.picking record
    # Updated every 5 minutes by the cron job in lalamove_data.xml
    lalamove_status = fields.Char(string='Lalamove Delivery Status', readonly=True)

    # Stop IDs returned by POST /v3/quotations
    # Required by POST /v3/orders — passed to stock_picking_ext.py during booking
    lalamove_sender_stop_id = fields.Char(string='Sender Stop ID', readonly=True)
    lalamove_recipient_stop_id = fields.Char(string='Recipient Stop ID', readonly=True)

    def action_get_lalamove_quote(self):
        """
        Retrieves a delivery price quotation from Lalamove API.
        Called when the user clicks 'Get Lalamove Quote' on a Sales Order.

        Flow:
        1. Validate config, company address, customer address, and phone
        2. Build POST /v3/quotations payload with pickup and delivery stops
        3. Call Lalamove API and parse the response
        4. Save quotation details and stop IDs on the sale.order record
        5. Distribute the Lalamove fee across order lines
        6. Recalculate margin per line factoring in the delivery cost

        Returns:
            dict: Odoo reload action to refresh the form view

        Raises:
            UserError: For missing config, incomplete address, missing phone,
                       duplicate quote, timeout, or API errors
        """

        # Step 1 — Verify Lalamove config exists
        config = self.env['lalamove.config'].search([], limit=1)
        if not config:
            raise UserError("Please configure Lalamove API credentials in Settings first.")

        # Step 2 — Validate company address (pickup location)
        company = self.env.company
        if not company.street or not company.city:
            raise UserError("Please complete your company address in Settings before requesting a quote.")

        # Step 3 — Validate customer address (delivery location)
        partner = self.partner_id
        if not partner.street or not partner.city:
            raise UserError("Please add a complete delivery address to the customer contact.")

        # Step 4 — Validate customer phone (required by Lalamove for recipient)
        if not partner.phone and not partner.mobile:
            raise UserError("Please add a phone number to the customer contact before requesting a quote.")

        # Build human-readable address strings for the API payload
        pickup_address = f"{company.street}, {company.city}"
        delivery_address = f"{partner.street}, {partner.city}"

        # Step 5 — Build POST /v3/quotations payload
        # Note: coordinates are currently hardcoded for sandbox testing
        # TODO: Replace with real geocoding for production deployment
        body_dict = {
            "data": {
                "serviceType": "MOTORCYCLE",
                "language": "en_PH",
                "stops": [
                    {
                        # Pickup — FAMTECH company location (hardcoded for sandbox)
                        "coordinates": {"lat": "14.5995", "lng": "120.9842"},
                        "address": pickup_address
                    },
                    {
                        # Delivery — customer address (hardcoded for sandbox)
                        "coordinates": {"lat": "14.5547", "lng": "121.0244"},
                        "address": delivery_address
                    }
                ]
            }
        }

        body = json.dumps(body_dict)

        # Step 6 — Call Lalamove POST /v3/quotations
        try:
            url = f'{config.base_url}/v3/quotations'
            headers = config.get_headers('POST', '/v3/quotations', body)
            response = requests.post(url, headers=headers, data=body, timeout=10)
        except requests.exceptions.Timeout:
            raise UserError("Could not reach Lalamove servers. Please try again.")
        except requests.exceptions.ConnectionError:
            raise UserError("Connection error. Please check your internet connection.")

        # Step 7 — Handle successful response
        if response.status_code == 201:
            data = response.json()['data']

            # Extract stop IDs from quotation response
            # These are required by POST /v3/orders during booking
            stops = data.get('stops', [])
            sender_stop_id = stops[0].get('stopId', '') if len(stops) > 0 else ''
            recipient_stop_id = stops[1].get('stopId', '') if len(stops) > 1 else ''

            # Parse delivery fee as float for margin calculations
            fee = float(data.get('priceBreakdown', {}).get('total', 0))

            # Prevent duplicate quotations on the same order
            if self.lalamove_fee:
                raise UserError("Lalamove fee already applied. Refresh or reset before re-quoting.")

            # Save quotation record linked to this sale order
            self.env['lalamove.quotation'].create({
                'sale_order_id': self.id,
                'total_fee': fee,
                'currency': data['priceBreakdown']['currency'],
                'service_type': data['serviceType'],
                'quotation_id': data['quotationId'],
                'expires_at': data['expiresAt'],
            })

            # Save quotation details on the sale.order record
            self.lalamove_quote_fee = f"{fee} {data['priceBreakdown']['currency']}"
            self.lalamove_quotation_id = data['quotationId']
            self.lalamove_fee = fee
            self.lalamove_quote_eta = data.get('expiresAt', '')

            # Save stop IDs — required for POST /v3/orders in stock_picking_ext.py
            self.lalamove_sender_stop_id = sender_stop_id
            self.lalamove_recipient_stop_id = recipient_stop_id

            # Step 8 — Distribute Lalamove fee across order lines
            # Each line gets an equal share of the total delivery fee
            if self.order_line:
                fee_per_line = fee / len(self.order_line)

                for line in self.order_line:
                    # Check if the line has 12% VAT applied
                    has_vat = any(
                        abs(t.amount - 12.0) < 0.01
                        for t in line.tax_id
                        if t.amount_type == 'percent'
                    )

                    # If VAT applies, use full fee share
                    # If no VAT, back-calculate the VAT-exclusive cost
                    if has_vat:
                        line.lalamove_cost = fee_per_line
                    else:
                        line.lalamove_cost = fee_per_line / 1.12

                    # Step 9 — Recalculate margin directly on each line
                    # Bypasses Odoo's native compute to include Lalamove cost
                    revenue = line.price_subtotal or 0.0
                    unit_cost = line.purchase_price or line.product_id.sudo().standard_price or 0.0
                    cost = unit_cost * (line.product_uom_qty or 0.0)

                    # Margin = Revenue - Product Cost - Lalamove Delivery Cost
                    new_margin = revenue - cost - line.lalamove_cost

                    # Write directly to bypass native compute method
                    line.write({
                        'margin': new_margin,
                        'margin_percent': (new_margin / revenue * 100) if revenue else 0.0,
                    })

            # Reload the form view to reflect all saved changes
            return {
                'type': 'ir.actions.client',
                'tag': 'reload',
            }

        # Step 10 — Handle API error responses
        elif response.status_code == 401:
            raise UserError("Invalid API credentials.")
        elif response.status_code == 422:
            raise UserError("Invalid address.")
        else:
            raise UserError(f"Failed to get quote: {response.status_code} - {response.text}")


# =============================================================================
# Sale Order Line Extension — Lalamove Cost and Margin
# =============================================================================
# Extends sale.order.line to add:
# 1. lalamove_cost — the share of the Lalamove delivery fee for this line
# 2. Margin recalculation that factors in the Lalamove delivery cost
#
# The native Odoo margin compute is overridden here so that the Lalamove
# delivery fee is included as a cost when calculating line-level margin.
# =============================================================================

class SaleOrderLineExt(models.Model):
    _inherit = 'sale.order.line'

    # Product cost price — used as the base for margin calculation
    purchase_price = fields.Float(string="Cost")

    # This line's share of the total Lalamove delivery fee
    # Set by action_get_lalamove_quote() in SaleOrderExt
    lalamove_cost = fields.Float(string="Lalamove Fee")

    # Override Odoo's native margin fields to include lalamove_cost
    # store=True ensures values persist in the database
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
        """
        Computes the margin per order line factoring in the Lalamove delivery cost.

        Formula:
            Margin = Revenue - Product Cost - Lalamove Delivery Cost

        Where:
            Revenue        = price_subtotal (always VAT-exclusive in Odoo)
            Product Cost   = purchase_price (or standard_price) × quantity
            Lalamove Cost  = this line's share of the total delivery fee

        Margin % = (Margin / Revenue) × 100
        """
        for line in self:
            # Revenue is always VAT-exclusive in Odoo (price_subtotal)
            revenue = line.price_subtotal or 0.0

            # Use purchase_price if set, otherwise fall back to product standard price
            unit_cost = line.purchase_price or line.product_id.sudo().standard_price or 0.0
            cost = unit_cost * (line.product_uom_qty or 0.0)

            # Include Lalamove delivery fee share in cost
            lalamove = line.lalamove_cost or 0.0

            # Margin = Revenue - Total Cost (product + delivery)
            line.margin = revenue - cost - lalamove

            # Margin % relative to revenue
            if revenue:
                line.margin_percent = (line.margin / revenue) * 100
            else:
                line.margin_percent = 0.0
