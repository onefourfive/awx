# Copyright (c) 2016 Ansible, Inc.
# All Rights Reserved.

# Python
import collections
import sys

# Django
from django.conf import settings
from django.core.urlresolvers import reverse
from django.http import Http404
from django.utils.translation import ugettext_lazy as _

# Django REST Framework
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework import serializers
from rest_framework import status

# Tower
from awx.api.generics import *  # noqa
from awx.main.utils import *  # noqa
from awx.conf.models import Setting
from awx.conf.serializers import SettingCategorySerializer, SettingSingletonSerializer
from awx.conf import settings_registry


SettingCategory = collections.namedtuple('SettingCategory', ('url', 'slug', 'name'))


class SettingCategoryList(ListAPIView):

    model = Setting  # Not exactly, but needed for the view.
    serializer_class = SettingCategorySerializer
    filter_backends = []
    new_in_310 = True
    view_name = _('Setting Categories')

    def get_queryset(self):
        setting_categories = []
        categories = settings_registry.get_registered_categories()
        if self.request.user.is_superuser or self.request.user.is_system_auditor:
            pass  # categories = categories
        elif 'user' in categories:
            categories = {'user', _('User')}
        else:
            categories = {}
        for category_slug in sorted(categories.keys()):
            url = reverse('api:setting_singleton_detail', args=(category_slug,))
            setting_categories.append(SettingCategory(url, category_slug, categories[category_slug]))
        return setting_categories


class SettingSingletonDetail(RetrieveUpdateDestroyAPIView):

    model = Setting  # Not exactly, but needed for the view.
    serializer_class = SettingSingletonSerializer
    filter_backends = []
    new_in_310 = True
    view_name = _('Setting Detail')

    def get_queryset(self):
        self.category_slug = self.kwargs.get('category_slug', 'all')
        all_category_slugs = settings_registry.get_registered_categories().keys()
        if self.request.user.is_superuser or getattr(self.request.user, 'is_system_auditor', False):
            category_slugs = all_category_slugs
        else:
            category_slugs = {'user'}
        if self.category_slug not in all_category_slugs:
            raise Http404
        if self.category_slug not in category_slugs:
            raise PermissionDenied()

        registered_settings = settings_registry.get_registered_settings(category_slug=self.category_slug, read_only=False)
        if self.category_slug == 'user':
            return Setting.objects.filter(key__in=registered_settings, user=self.request.user)
        else:
            return Setting.objects.filter(key__in=registered_settings, user__isnull=True)

    def get_object(self):
        settings_qs = self.get_queryset()
        registered_settings = settings_registry.get_registered_settings(category_slug=self.category_slug)
        all_settings = {}
        for setting in settings_qs:
            all_settings[setting.key] = setting.value
        for key in registered_settings:
            if key in all_settings or self.category_slug == 'changed':
                continue
            try:
                field = settings_registry.get_setting_field(key, for_user=bool(self.category_slug == 'user'))
                all_settings[key] = field.get_default()
            except serializers.SkipField:
                all_settings[key] = None
        all_settings['user'] = self.request.user if self.category_slug == 'user' else None
        obj = type('Settings', (object,), all_settings)()
        self.check_object_permissions(self.request, obj)
        return obj

    def perform_update(self, serializer):
        settings_qs = self.get_queryset()
        user = self.request.user if self.category_slug == 'user' else None
        for key, value in serializer.validated_data.items():
            if key == 'LICENSE':
                continue
            setattr(serializer.instance, key, value)
            setting = settings_qs.filter(key=key).order_by('pk').first()
            if not setting:
                setting = Setting.objects.create(key=key, user=user, value=value)
            elif setting.value != value or type(setting.value) != type(value):
                setting.value = value
                setting.save(update_fields=['value'])

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def perform_destroy(self, instance):
        for setting in self.get_queryset().exclude(key='LICENSE'):
            setting.delete()

        # When TOWER_URL_BASE is deleted from the API, reset it to the hostname
        # used to make the request as a default.
        if hasattr(instance, 'TOWER_URL_BASE'):
            url = '{}://{}'.format(self.request.scheme, self.request.get_host())
            if settings.TOWER_URL_BASE != url:
                settings.TOWER_URL_BASE = url


# Create view functions for all of the class-based views to simplify inclusion
# in URL patterns and reverse URL lookups, converting CamelCase names to
# lowercase_with_underscore (e.g. MyView.as_view() becomes my_view).
this_module = sys.modules[__name__]
for attr, value in locals().items():
    if isinstance(value, type) and issubclass(value, APIView):
        name = camelcase_to_underscore(attr)
        view = value.as_view()
        setattr(this_module, name, view)
