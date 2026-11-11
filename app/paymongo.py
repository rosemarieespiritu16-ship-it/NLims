import os
import requests


def _secret():
    return os.getenv('PAYMONGO_SECRET_KEY')


def create_payment_link(amount, description, reference='', success_url=None, failure_url=None):
    secret = _secret()
    if not secret:
        return {'success': False, 'error': 'PAYMONGO_SECRET_KEY not set.'}

    url = 'https://api.paymongo.com/v1/links'
    amount_cents = int(round(amount * 100))
    if amount_cents < 100:
        return {'success': False, 'error': 'Amount must be at least PHP 1.00 (100 centavos).'}

    attrs = {
        'amount': amount_cents,
        'currency': 'PHP',
        'description': description[:1000] or 'InventorPro',
        'remarks': reference[:1000] or 'InventorPro Payment'
    }
    if success_url or failure_url:
        attrs['redirect'] = {}
        if success_url:
            attrs['redirect']['success'] = success_url
        if failure_url:
            attrs['redirect']['failed'] = failure_url

    payload = {'data': {'attributes': attrs}}

    try:
        r = requests.post(url, auth=(secret, ''), json=payload, timeout=30)
        r.raise_for_status()
        data = r.json()['data']
        attrs = data['attributes']
        return {
            'success': True,
            'checkout_url': attrs['checkout_url'],
            'link_id': data['id'],
            'reference_number': attrs.get('reference_number', '')
        }
    except requests.exceptions.HTTPError:
        try:
            detail = r.json()
        except Exception:
            detail = r.text
        return {'success': False, 'error': f'{r.status_code}: {detail}'}
    except requests.exceptions.RequestException as e:
        return {'success': False, 'error': str(e)}


def get_payment_link(link_id):
    secret = _secret()
    if not secret:
        return {'success': False, 'error': 'PAYMONGO_SECRET_KEY not set.'}

    url = f'https://api.paymongo.com/v1/links/{link_id}'
    try:
        r = requests.get(url, auth=(secret, ''), timeout=30)
        r.raise_for_status()
        return {'success': True, 'data': r.json()['data']}
    except requests.exceptions.HTTPError:
        try:
            detail = r.json()
        except Exception:
            detail = r.text
        return {'success': False, 'error': f'{r.status_code}: {detail}'}
    except requests.exceptions.RequestException as e:
        return {'success': False, 'error': str(e)}


def is_link_paid(link_id):
    result = get_payment_link(link_id)
    if not result.get('success'):
        return False
    attrs = result['data'].get('attributes', result['data'])
    return attrs.get('status') == 'paid' or len(attrs.get('payments', [])) > 0
