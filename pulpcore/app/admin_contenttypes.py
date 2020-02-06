# -*- coding: utf-8 -*-
from django.contrib import admin

from .models import ContentType


@admin.register(ContentType)
class ContentTypeAdmin(admin.ModelAdmin):
    list_display = ('id', 'app_label', 'model')
