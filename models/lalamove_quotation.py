from odoo import models, fields

class LalamoveQuotation(models.Model):
    _name = 'lalamove.quotation'
    _description = 'Lalamove Delivery Quotation'

    sale_order_id = fields.Many2one('sale.order', string='Sales Order')
    total_fee = fields.Char(string='Total Fee')
    currency = fields.Char(string='Currency')
    service_type = fields.Char(string='Service Type')
    eta = fields.Char(string='ETA')
    quotation_id = fields.Char(string='Quotation ID')
    expires_at = fields.Char(string='Expires At')
    # Stop IDs needed for booking payload
    sender_stop_id = fields.Char(string='Sender Stop ID')
    recipient_stop_id = fields.Char(string='Recipient Stop ID')

class SaleOrderLalamove(models.Model):
    _inherit = 'sale.order'

    # Quotation fields
    lalamove_quotation_id = fields.Char(string='Lalamove Quotation ID')
    lalamove_quote_fee = fields.Char(string='Delivery Fee')
    lalamove_quote_eta = fields.Char(string='ETA')

    # These come from stock.picking via related field
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

    # Stop IDs stored from quotation response
    # needed by lalamove_order.py for booking payload
    lalamove_sender_stop_id = fields.Char(string='Sender Stop ID')
    lalamove_recipient_stop_id = fields.Char(string='Recipient Stop ID')
