"""Bounded, local return context for list/detail/edit navigation."""
from urllib.parse import parse_qsl, urlencode, urlsplit
from django.urls import Resolver404, resolve, reverse

LISTS = {
    'entity_list': {'date_from','date_to','borough','origin','active','source_id','q','category','sort','severity','vehicle_type','person_type','position','collision_id','vehicle_id','type','injury','age_min','age_max','page'},
    'quality_list': {'status','source','code','severity','record_id','page'},
    'network_catalog': {'network','state','q','borough','page'},
    'traffic_sites': {'q','borough','direction','request','road','inactive','page'},
    'analysis_records': {'date_from','date_to','borough','network','road','junction','year','month','weekday','hour','severity','dimension','metric','bbox','bucket_dimension','bucket_key','page'},
    'spatial_list': {'kind','network','status','borough','q','page'},
}


def safe_list_url(value, fallback=''):
    if not isinstance(value, str) or len(value) > 4096 or not value.startswith('/') or value.startswith('//'):
        return fallback
    if any(ord(c) < 32 for c in value) or '\\' in value:
        return fallback
    parts = urlsplit(value)
    if parts.scheme or parts.netloc or parts.fragment:
        return fallback
    try:
        match = resolve(parts.path)
    except Resolver404:
        return fallback
    if match.url_name not in LISTS:
        return fallback
    if match.url_name in ('entity_list', 'analysis_records') and match.kwargs.get('kind') not in ('crashes','vehicles','persons'):
        return fallback
    if match.url_name == 'network_catalog' and match.kwargs.get('kind') not in ('road','junction'):
        return fallback
    # Unknown parameters are discarded, repeated parameters resolve as a form does.
    try:
        params = {k:v for k,v in parse_qsl(parts.query, max_num_fields=80) if k in LISTS[match.url_name] and len(v) <= 200}
    except ValueError:
        return fallback
    return parts.path + ('?' + urlencode(params) if params else '')


def return_context(request):
    match = request.resolver_match
    name = match.url_name if match else ''
    kind = match.kwargs.get('kind', 'crashes') if match else 'crashes'
    if name.startswith('quality_'):
        fallback = reverse('quality_list')
    elif name.startswith('network_'):
        fallback = reverse('traffic_sites') if kind == 'site' else reverse('network_catalog', args=[kind])
    else:
        fallback = reverse('entity_list', args=[kind if kind in ('crashes','vehicles','persons') else 'crashes'])
    try:
        target = safe_list_url(request.GET.get('return_to', ''), fallback)
        current_list = safe_list_url(request.get_full_path(), target)
    except ValueError:
        target = current_list = fallback
    return {'return_to': target, 'return_query': urlencode({'return_to': target}),
            'list_return_query': urlencode({'return_to': current_list})}


def detail_url(request, name, **kwargs):
    return reverse(name, kwargs=kwargs) + '?' + return_context(request)['return_query']
