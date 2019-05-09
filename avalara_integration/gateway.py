import logging
import pprint
import base64

from django.conf import settings
import json
import purl
import requests

from avalara_integration import models, exceptions

# __all__ = ['get_tax', 'post_tax']
__all__ = ['post_tax',]

logger = logging.getLogger('avalara')

# URL templates
URL_TEMPLATES = {
    'post_tax': purl.Template('/api{/version}/transactions/create'),
}

AVALARA_API_VERSION = 'v2'

def fetch(method, url_template, url_params=None, payload=None):
    """
    Make a HTTP round-trip to Avalara
    """
    # Build URL
    if url_params is None:
        url_params = {}
    url_params['version'] = AVALARA_API_VERSION
    url = url_template.expand(url_params)
    host = settings.AVALARA_ENDPOINT
    url = url.scheme('https').host(host).as_string()

    # Make request
    headers = {'Accept': 'application/json'}
    payload_json = None
    if payload:
        # logger.debug('Submitting payload:', pprint.pformat(payload))
        # print('Submitting payload:', pprint.pformat(payload))
        headers['Content-type'] = 'application/json'
        payload_json = json.dumps(payload)

    response = requests.request(
        method,
        url,
        auth=(settings.AVALARA_ACCOUNT_NUMBER,settings.AVALARA_LICENSE_KEY),
        data=payload_json,
        headers=headers
    )
    data = response.json()

    # logger.info('Response JSON: ', pprint.pformat(data))
    # print('Response JSON: ', pprint.pformat(data))

    # Save audit model
    models.Request.objects.create(
        account_number=settings.AVALARA_ACCOUNT_NUMBER,
        method=method,
        url=url,
        request=payload_json or '',
        response=response.text,
        )

    # Handle errors
    if not response.ok:
        raise exceptions.AvalaraError(data['error']['message'])

    # success
    return data


# def get_tax(coords, amount):
#     """
#     Fetch tax details for a given location and amount

#     http://developer.avalara.com/api-docs/rest/tax/get
#     """
#     params = {
#         'location': ",".join(coords),
#         'saleamount': amount
#     }
#     return fetch('GET', URL_TEMPLATES['get_tax'], params)


def post_tax(payload):
    """
    Fetch/commit tax details for a basket

    http://developer.avalara.com/api-docs/rest/tax/post
    """
    return fetch('POST', URL_TEMPLATES['post_tax'], payload=payload)
