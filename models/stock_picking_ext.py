# =============================================================================
# Stock Picking Extension — Lalamove Booking and Status Sync
# =============================================================================
# Extends stock.picking (Delivery Orders) with:
# 1. Lalamove-specific fields for storing booking results
# 2. _format_phone_e164() — converts PH phone numbers to E.164 format
# 3. action_book_lalamove() — calls POST /v3/orders to book a delivery
# 4. sync_all_lalamove_orders() — called by cron job every 5 minutes
#    to poll GET /v3/orders/{orderId} and update delivery status
#
# Note: lalamove_order.py was removed and its logic consolidated here
# since all booking and sync operations act directly on stock.picking.
#
# Dependencies:
# - lalamove_config.py — provides get_headers() for API authentication
# - sale_order_ext.py — provides lalamove_quotation_id and stop IDs
#   saved during action_get_lalamove_quote()
# =============================================================================

from odoo import models, fields
from odoo.exceptions import UserError
import json, requests, re

class StockPickingExt(models.Model):
    _inherit = 'stock.picking'

    # 19-digit Lalamove order ID returned by POST /v3/orders
    # Stored as Char — must NOT be Integer (19 digits exceeds int range)
    lalamove_order_id = fields.Char(string='Lalamove Order ID')

    # Shareable tracking URL (shareLink) returned by POST /v3/orders
    # Displayed as a clickable link in the Lalamove Delivery tab
    lalamove_tracking_url = fields.Char(string='Lalamove Tracking URL')

    # Current delivery status — updated every 5 mins by the cron job
    # Possible values: ASSIGNING_DRIVER, ON_GOING, PICKED_UP,
    #                  COMPLETED, CANCELED, REJECTED, EXPIRED
    lalamove_status = fields.Char(string='Lalamove Delivery Status')

    # Driver details — populated after a driver is assigned by Lalamove
    lalamove_driver_name = fields.Char(string='Driver Name')
    lalamove_driver_phone = fields.Char(string='Driver Phone')

    def _format_phone_e164(self, raw_phone):
        """
        Converts any Philippine phone number format to E.164 format.
        Lalamove requires all phone numbers in E.164: +639XXXXXXXXX

        Handles these input formats:
            09171234567   → +639171234567  (local format)
            639171234567  → +639171234567  (missing + prefix)
            +639171234567 → +639171234567  (already correct)
            0917 123 4567 → +639171234567  (with spaces)

        Args:
            raw_phone (str): Phone number in any format

        Returns:
            str: Phone number in E.164 format (+639XXXXXXXXX)
        """
        # Remove all whitespace
        phone = re.sub(r'\s+', '', raw_phone)

        # Remove all non-numeric characters except leading +
        phone = re.sub(r'[^\d+]', '', phone)

        if phone.startswith('0'):
            # Local format: 09XX → +639XX
            phone = '+63' + phone[1:]
        elif phone.startswith('63'):
            # Missing + prefix: 639XX → +639XX
            phone = '+' + phone
        elif not phone.startswith('+'):
            # No country code at all — assume PH
            phone = '+63' + phone

        return phone

    def action_book_lalamove(self):
        """
        Books a Lalamove delivery order for this stock.picking record.
        Called when the user clicks 'Book via Lalamove' on a Delivery Order.

        Prerequisites (validated before API call):
        - Lalamove config must exist in Settings
        - Delivery must be linked to a Sales Order
        - Sales Order must have a valid lalamove_quotation_id
        - Sales Order must have sender and recipient stop IDs
          (saved during action_get_lalamove_quote() in sale_order_ext.py)
        - Customer must have a phone number
        - Company must have a phone number

        Flow:
        1. Validate all prerequisites
        2. Format phone numbers to E.164
        3. Build POST /v3/orders payload
        4. Call Lalamove API
        5. Save orderId, status, and shareLink on this stock.picking record
        6. Reload the delivery order form

        Returns:
            dict: Odoo act_window action to reload the delivery form

        Raises:
            UserError: For missing config, missing sale order, missing quote,
                       missing stop IDs, missing phone, timeout, or API errors
        """
        # Ensure this method runs on a single record only
        self.ensure_one()

        # Step 1 — Verify Lalamove config exists
        config = self.env['lalamove.config'].search([], limit=1)
        if not config:
            raise UserError("Please configure Lalamove API credentials in Settings first.")

        # Step 2 — Verify this delivery is linked to a Sales Order
        sale_order = self.sale_id
        if not sale_order:
            raise UserError("No Sales Order linked to this delivery.")

        # Step 3 — Verify quotation ID exists on the Sales Order
        # This is set by action_get_lalamove_quote() in sale_order_ext.py
        if not sale_order.lalamove_quotation_id:
            raise UserError("No Lalamove quote found. Please get a quote on the Sales Order first.")

        # Step 4 — Verify stop IDs exist
        # Stop IDs are returned by POST /v3/quotations and saved on sale.order
        # They are required by POST /v3/orders
        if not sale_order.lalamove_sender_stop_id or not sale_order.lalamove_recipient_stop_id:
            raise UserError("Stop IDs missing. Please get a fresh Lalamove quote on the Sales Order.")

        # Step 5 — Get and format customer phone to E.164
        partner = self.partner_id
        raw_phone = partner.phone or partner.mobile or ''
        if not raw_phone:
            raise UserError("Customer has no phone number. Please add one to the contact.")
        customer_phone = self._format_phone_e164(raw_phone)

        # Step 6 — Get and format company phone to E.164 (sender)
        company = self.env.company
        raw_company_phone = company.phone or ''
        if not raw_company_phone:
            raise UserError("Company has no phone number. Please add one in Settings > Companies.")
        sender_phone = self._format_phone_e164(raw_company_phone)

        # Step 7 — Build POST /v3/orders payload
        body_dict = {
            "data": {
                # quotationId from the previous GET /v3/quotations call
                "quotationId": sale_order.lalamove_quotation_id,
                "sender": {
                    # stopId saved from quotation response stops[0]
                    "stopId": sale_order.lalamove_sender_stop_id,
                    "name": company.name,
                    "phone": sender_phone,
                },
                "recipients": [
                    {
                        # stopId saved from quotation response stops[1]
                        "stopId": sale_order.lalamove_recipient_stop_id,
                        "name": partner.name,
                        "phone": customer_phone,
                    }
                ],
                # Proof of delivery not required for this integration
                "isPODEnabled": False,
            }
        }
        body = json.dumps(body_dict)

        # Step 8 — Call Lalamove POST /v3/orders
        try:
            url = f'{config.base_url}/v3/orders'
            headers = config.get_headers('POST', '/v3/orders', body)
            response = requests.post(url, headers=headers, data=body, timeout=10)
        except requests.exceptions.Timeout:
            raise UserError("Could not reach Lalamove servers. Please try again.")
        except requests.exceptions.ConnectionError:
            raise UserError("Connection error. Please check your internet connection.")

        # Step 9 — Handle API response
        if response.status_code == 201:
            data = response.json()['data']

            # Save booking results on this stock.picking record
            # orderId must be stored as Char — 19 digits exceeds int range
            self.write({
                'lalamove_order_id': data.get('orderId', ''),
                'lalamove_status': data.get('status', ''),       # initial: ASSIGNING_DRIVER
                'lalamove_tracking_url': data.get('shareLink', ''),
            })

            # Reload the delivery order form to show updated fields
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'stock.picking',
                'res_id': self.id,
                'view_mode': 'form',
                'target': 'current',
            }

        # Handle error responses with specific messages
        elif response.status_code == 400:
            raise UserError(f"Lalamove booking failed: {response.json()}")
        elif response.status_code == 401:
            raise UserError("Invalid API credentials.")
        else:
            raise UserError(f"Booking failed: {response.status_code} - {response.text}")

    def sync_all_lalamove_orders(self):
        """
        Polls Lalamove API for the latest status of all active deliveries.
        Called automatically every 5 minutes by the cron job in lalamove_data.xml.

        Flow:
        1. Find all stock.picking records with an active Lalamove booking
           (has lalamove_order_id and non-final status)
        2. For each picking, call GET /v3/orders/{orderId}
        3. Update lalamove_status with the returned status
        4. If COMPLETED → validate the delivery in Odoo (mark as done)
        5. If CANCELED → cancel the delivery in Odoo
        6. Skip failed requests and continue with remaining pickings

        Status mapping:
            ASSIGNING_DRIVER → update field only
            ON_GOING         → update field only
            PICKED_UP        → update field only
            COMPLETED        → button_validate() + update field
            CANCELED         → action_cancel() + update field
            REJECTED         → update field only (flag for resubmission)
            EXPIRED          → update field only (no driver found)
        """

        # Step 1 — Find all stock.picking records with active Lalamove bookings
        # Excludes records with final statuses to avoid unnecessary API calls
        active_pickings = self.search([
            ('lalamove_order_id', '!=', False),
            ('lalamove_status', 'not in', [
                'COMPLETED', 'CANCELED', 'REJECTED', 'EXPIRED', False
            ])
        ])

        # Nothing to sync — exit silently
        if not active_pickings:
            return

        # Step 2 — Verify Lalamove config exists before making any API calls
        config = self.env['lalamove.config'].search([], limit=1)
        if not config:
            return

        # Step 3 — Poll each active delivery order
        for picking in active_pickings:
            order_id = picking.lalamove_order_id
            path = f'/v3/orders/{order_id}'
            url = f'{config.base_url}{path}'
            headers = config.get_headers('GET', path)

            try:
                response = requests.get(url, headers=headers, timeout=10)
            except requests.exceptions.RequestException:
                # Skip this picking on network error — retry on next cron run
                continue

            if response.status_code == 200:
                data = response.json().get('data', {})
                status = data.get('status', '')

                # Step 4 — Map Lalamove status to Odoo delivery actions
                if status == 'COMPLETED':
                    try:
                        # Mark delivery as done in Odoo
                        picking.with_context(skip_immediate=True).button_validate()
                    except Exception:
                        # Skip if validation fails (e.g. missing quantities)
                        pass
                elif status == 'CANCELED':
                    try:
                        # Mark delivery as cancelled in Odoo
                        picking.action_cancel()
                    except Exception:
                        # Skip if cancellation fails
                        pass

                # Step 5 — Update status field regardless of Odoo action result
                picking.write({'lalamove_status': status})
