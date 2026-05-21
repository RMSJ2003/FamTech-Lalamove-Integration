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