import pprint
from decimal import Decimal as D

from django.db import models
import json


class Request(models.Model):
    """
    Audit model for tracking requests
    """
    account_number = models.CharField(max_length=64)
    method = models.CharField(max_length=32, default="GET")
    url = models.CharField(max_length=255)
    request = models.TextField(blank=True)
    response = models.TextField()

    date_created = models.DateTimeField(auto_now_add=True)

    def __unicode__(self):
        return u'%s request, result: %s' % (
            self.doc_type, self.result_code)

    class Meta:
        ordering = ('-date_created',)

    @property
    def doc_code(self):
        data = json.loads(self.request)
        if 'code' in data:
            return data['code']

    @property
    def doc_type(self):
        data = json.loads(self.request)
        if 'type' in data:
            return data['type']

    @property
    def result_code(self):
        data = json.loads(self.response)
        if 'code' in data:
            return data['code']

    # @property
    # def error_message(self):
    #     data = json.loads(self.response)
    #     if 'Messages' in data:
    #         return data['Messages'][0]['Summary']
    #     return ''

    @property
    def total_taxable(self):
        data = json.loads(self.response)
        if 'totalTaxable' in data:
            return D(data['totalTaxable'])

    @property
    def total_tax(self):
        data = json.loads(self.response)
        if 'totalTax' in data:
            return str(round(data['totalTax'], 2))


    def request_html(self):
        data = json.loads(self.response)
        return '<br/><pre>%s</pre>' % pprint.pformat(data)
    request_html.allow_tags = True

    def response_html(self):
        data = json.loads(self.response)
        return '<br/><pre>%s</pre>' % pprint.pformat(data)
    response_html.allow_tags = True
