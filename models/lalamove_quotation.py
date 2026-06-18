# =============================================================================
# Lalamove Quotation Model
# =============================================================================
# Stores the quotation results returned by the Lalamove API when a user
# clicks "Get Lalamove Quote" on a Sales Order.
#
# Each quotation record is linked to its parent Sales Order and contains
# the pricing and service details returned by POST /v3/quotations.
#
# The quotationId stored here is passed to POST /v3/orders during booking.
#
# Important: Lalamove quotations are only valid for 5 minutes after creation.
# If the user does not book within that window, a new quotation must be
# requested before booking can proceed.
#
# Records are created automatically by action_get_lalamove_quote()
# in sale_order_ext.py — they are not created manually by users.
# =============================================================================

from odoo import models, fields

class LalamoveQuotation(models.Model):
    _name = 'lalamove.quotation'
    _description = 'Lalamove Delivery Quotation'

    # Links this quotation record to its parent Sales Order
    sale_order_id = fields.Many2one('sale.order', string='Sales Order')

    # Total delivery fee returned by POST /v3/quotations
    # Stored as Char to preserve formatting (e.g. "90")
    total_fee = fields.Char(string='Total Fee')

    # Currency code returned by the API (e.g. "PHP")
    currency = fields.Char(string='Currency')

    # Lalamove service type used for this quotation (e.g. "MOTORCYCLE")
    # Service type keys must be retrieved via GET /v3/cities before use
    service_type = fields.Char(string='Service Type')

    # Estimated time of arrival returned by the quotation response
    eta = fields.Char(string='ETA')

    # Unique quotation ID returned by POST /v3/quotations
    # This is required as input when calling POST /v3/orders to book
    # Valid for 5 minutes only — expires after that
    quotation_id = fields.Char(string='Quotation ID')

    # Expiration timestamp of the quotation in UTC format
    # After this time, a new quotation must be requested
    expires_at = fields.Char(string='Expires At')
