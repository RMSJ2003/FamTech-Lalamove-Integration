{
    'name': 'FAMTECH Lalamove Integration',
    'version': '1.0',
    'depends': ['sale', 'stock', 'delivery'],
    'data': [
        'security/ir.model.access.csv',
        'data/lalamove_data.xml',
        'views/lalamove_config_views.xml',
        'views/sale_order_views.xml',
        'views/stock_picking_views.xml',
    ]
}
