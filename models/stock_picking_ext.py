from odoo import models, fields

class StockPickingExt(models.Model):
    _inherit = 'stock.picking'

    lalamove_order_id = fields.Char(string='Lalamove Order ID')
    lalamove_tracking_url = fields.Char(string='Tracking URL')
    lalamove_status = fields.Char(string='Delivery Status')
    lalamove_driver_name = fields.Char(string='Driver Name')
    lalamove_driver_phone = fields.Char(string='Driver Phone')