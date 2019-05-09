"""
Bridge module between Oscar and the core Avalara functionality
"""
import logging
import datetime
from decimal import Decimal as D
import zlib
import pprint

from django.core.cache import cache
from django.core import exceptions
from django.conf import settings
from oscar.core.loading import get_class, get_model

from avalara_integration import gateway
from avalara_integration import exceptions as avalara_exceptions

OrderTotalCalculator = get_class(
    'checkout.calculators', 'OrderTotalCalculator')
OrderLine = get_model('order', 'Line')
ShippingAddress = get_model('order', 'ShippingAddress')

__all__ = ['apply_taxes_to_submission', 'apply_taxes', 'submit', 'fetch_tax_info']

logger = logging.getLogger('avalara')


def apply_taxes_to_submission(submission):
    """
    Apply taxes to a submission dict.

    This is designed to work seamlessly with the PaymentDetailsView of Oscar's
    checkout.
    """
    # print('applying taxes now')
    # print(dict(submission['shipping_address']))
    # this was breaking things, just always check tax i guess
    # if submission['basket'].is_tax_known:
    #     print('tax is known')
    #     return
    apply_taxes(
        submission['user'],
        submission['basket'],
        submission['shipping_address'],
        submission['shipping_method'],
        submission['shipping_charge'])

    # Update order total
    submission['order_total'] = OrderTotalCalculator().calculate(
        submission['basket'], submission['shipping_charge'])


def apply_taxes(user, basket, shipping_address, shipping_method, shipping_charge):
    """
    Apply taxes to the basket and shipping charge
    """
    data = fetch_tax_info(user, basket, shipping_address, shipping_method, shipping_charge)

    # Build hash table of line_id => tax
    line_taxes = {}
    for tax_line in data['lines']:
        print('line: ', tax_line['lineNumber'], '- tax:', tax_line['tax'])
        line_taxes[tax_line['lineNumber']] = D(tax_line['tax'])

    # Apply these tax values to the basket and shipping method.
    for line in basket.all_lines():
        line_id = str(line.id)
        if line_id not in line_taxes:
            raise RuntimeError("Unable to determine taxes on basket #%s" %
                               basket.id)

        # Avalara gives us the tax for the whole line, but we want it at
        # a unit level so we divide by the quantity.  This can lead to the unit
        # tax having more than 2 decimal places.  This isn't a problem
        # (AFAICT): we don't truncate at this stage but assign the correct
        # decimal as the tax so that the total line tax is correct.  Rounding
        # will occur when unit_tax_incl_tax is calculated for the Order.Line
        # model but that isn't a problem.
        unit_tax = line_taxes[str(line.id)] / line.quantity
        line.purchase_info.price.tax = unit_tax
    shipping_charge.tax = line_taxes['SHIPPING']


def submit(order):
    """
    Submit tax information from an order
    """
    # shipping address comes through order as id but we need object
    shipping_address = ShippingAddress.objects.get(id=order.shipping_address_id)

    payload = _build_payload(
        'SalesInvoice',
        order.number,
        order.user,
        order.lines.all(),
        shipping_address.__dict__, # this model defaults to a string but we want the dict
        order.shipping_address,
        str(order.shipping_method),
        order.shipping_excl_tax,
        commit=True,
    )
    gateway.post_tax(payload)


# def fetch_tax_info_for_order(order):
#     """
#     Fetch tax info retrospectively for order.

#     This is for debugging tax issues.
#     """
#     payload = _build_payload(
#         'SalesOrder',
#         order.number,
#         order.user,
#         order.lines.all(),
#         order.shipping_address,
#         order.shipping_method,
#         order.shipping_charge,
#         commit=False)
#     gateway.post_tax(payload)


def fetch_tax_info(user, basket, shipping_address, shipping_method, shipping_charge):
    # Look for a cache hit first
    payload = _build_payload(
        'SalesOrder',
        'basket-%d' % basket.id,
        user,
        basket.all_lines(),
        shipping_address,
        frozenset(shipping_address),
        str(shipping_method.name),
        shipping_charge.excl_tax,
        commit=False,
    )

    # # not going to use caching
    # key = _build_cache_key(payload)
    # data = cache.get(key)
    # if not data:
    #     logger.debug("Cache miss - fetching data")
    #     data = gateway.post_tax(payload)
    #     cache.set(key, data, timeout=None)
    # else:
    #     logger.debug("Cache hit")
    # return data
    
    data = gateway.post_tax(payload)

    return data



def _build_payload(doc_type, doc_code, user, lines, shipping_address, shipping_address_string,
                   shipping_method, shipping_charge, commit):
    payload = {}

    # Use a single company code for now
    payload['CompanyCode'] = settings.AVALARA_COMPANY_CODE

    payload['date'] = datetime.date.today().strftime("%Y-%m-%d")
    if user and user.id:
        payload['customerCode'] = 'customer-%d' % user.id
    else:
        payload['customerCode'] = 'anonymous'
    payload['code'] = doc_code
    payload['type'] = doc_type
    # payload['DetailLevel'] = 'Line'
    # payload['Commit'] = commit
    payload['lines'] = []
    payload['addresses'] = {}

    # Customer address
    address_code = hash(shipping_address_string) # create code to add to dict of addresses we have already seen

    # submission country = 'country'
    # order country = 'country_id'
    try:
        country = shipping_address['country']
        country = country.iso_3166_1_a2
    except Exception as e:
        pass

    try:
        country = shipping_address['country_id']
    except Exception as e:
        pass

    if 'country' in shipping_address:
        country = shipping_address['country'].iso_3166_1_a2
    elif 'country_id' in shipping_address:
        country = shipping_address['country_id']
    else:
        raise avalara_exceptions.AvalaraError('could not find country in order or submisison')


    ship_to = {
        'line1': shipping_address['line1'],
        'Line2': shipping_address['line2'],
        'city': shipping_address['line4'],
        'region': shipping_address['state'],
        'country': str(country),
        'postalCode': shipping_address['postcode'],
    }
    payload['addresses']['shipTo'] = ship_to

    # Lines
    partner_address_codes = []
    for line in lines:
        product = line.product
        record = line.stockrecord

        # if tax category is not set on an item, send empty string
        try:
            tax_category = line.product.attr.tax_category
        except Exception as e:
            tax_category = ''
            pass

        line_payload = {
            'number': line.id,
            'quantity': line.quantity,
            # 'DestinationCode': address_code,
            # 'OriginCode': partner_address_code,
            'taxCode': tax_category,
            'itemCode': record.partner_sku,
            'description': product.description[:255] if product.description else '',
            'addresses': {}, # line level addresses to ship from diff warehouses
        }
        # We distinguish between order and basket lines (which have slightly
        # different APIs).
        if isinstance(line, OrderLine):
            line_payload['Amount'] = str(line.line_price_excl_tax)
        else:
            line_payload['Amount'] = str(line.line_price_excl_tax_incl_discounts)

        # Ensure origin address in in Addresses collection
        partner_address = record.partner.primary_address
        if not partner_address:
            raise exceptions.ImproperlyConfigured((
                "You need to create a primary address for partner %s "
                "in order for Avalara to be able to calculate taxes") %
                record.partner)

        line_ship_from = {
            'line1': partner_address.line1,
            'line2': partner_address.line2,
            'city': partner_address.city,
            'region': partner_address.state,
            'country': partner_address.country.iso_3166_1_a2,
            'postalCode': partner_address.postcode,
        }

        # see if this is already default address
        partner_address_code = hash(partner_address)
        if partner_address_code not in partner_address_codes:

            partner_address_codes.append(partner_address_code)
            # use first item as default from address
            line_payload['addresses']['shipFrom'] = line_ship_from


        payload['lines'].append(line_payload)

    # Shipping (treated as another line).  We assume origin address is the
    # first partner address
    line = {
        'number': 'SHIPPING',
        'destinationCode': address_code,
        'originCode': partner_address_codes[0],
        'itemCode': '',
        'Description': shipping_method,
        'quantity': 1,
        'amount': str(shipping_charge),
        'taxCode': 'FR',  # Special code for shipping
    }
    payload['lines'].append(line)

    return payload

# # not going to use caching
# def _build_cache_key(payload):
#     """
#     Build a caching key based on a given payload.  The key should change if any
#     part of the basket or shipping address changes.
#     """
#     parts = []

#     for address in payload['Addresses']:
#         parts.append(str(address['AddressCode']))

#     for line in payload['Lines']:
#         parts.extend([line['Amount'], line['ItemCode'], str(line['Qty']), str(line['LineNo'])])

#     joined = '-'.join(parts)
#     print(joined)
#     joined = joined.encode()

#     return "avalara-%s" % zlib.crc32(joined)
