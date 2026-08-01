from __future__ import annotations

from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from chat.models import Conversation, Message


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = [
            "seq", "role", "content", "tool_calls", "citations", "citation_mode",
            "latency_ms", "model_id", "is_partial", "error", "created_at",
        ]


class ConversationSerializer(serializers.ModelSerializer):
    message_count = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = [
            "id", "title", "scope", "document_ids", "message_count",
            "created_at", "updated_at",
        ]

    def get_message_count(self, obj) -> int:
        return obj.messages.count()


class ConversationDetailSerializer(ConversationSerializer):
    messages = MessageSerializer(many=True, read_only=True)

    class Meta(ConversationSerializer.Meta):
        fields = ConversationSerializer.Meta.fields + ["messages"]


class ConversationViewSet(viewsets.ModelViewSet):
    queryset = Conversation.objects.prefetch_related("messages").all()

    def get_serializer_class(self):
        return (
            ConversationSerializer if self.action == "list" else ConversationDetailSerializer
        )

    @action(detail=True, methods=["get"])
    def messages(self, request, pk=None):
        conversation = self.get_object()
        return Response(MessageSerializer(conversation.messages.all(), many=True).data)
