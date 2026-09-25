from __future__ import annotations

from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from chat.attachments import serialize as serialize_attachment
from chat.models import Conversation, Message
from chat.research import DEFAULT_CHAT_MODE
from common.ownership import CHAT_READY_STATUSES, owned_documents


class MessageSerializer(serializers.ModelSerializer):
    chat_mode = serializers.SerializerMethodField()
    thinking = serializers.SerializerMethodField()
    follow_ups = serializers.SerializerMethodField()
    phases = serializers.SerializerMethodField()
    attachments = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = [
            "seq", "role", "content", "tool_calls", "citations", "citation_mode",
            "chat_mode", "thinking", "reasoning", "thinking_ms", "follow_ups", "phases",
            "attachments", "latency_ms", "model_id", "is_partial", "error", "created_at",
        ]

    def get_chat_mode(self, obj) -> str:
        return (obj.usage or {}).get("chat_mode", DEFAULT_CHAT_MODE)

    def get_thinking(self, obj) -> str:
        return (obj.usage or {}).get("thinking", "instant")

    def get_follow_ups(self, obj) -> list[str]:
        return (obj.usage or {}).get("follow_ups") or []

    def get_phases(self, obj) -> dict | None:
        return (obj.usage or {}).get("phases")

    def get_attachments(self, obj) -> list[dict]:
        return [serialize_attachment(a) for a in obj.attachments.all()]


class ConversationSerializer(serializers.ModelSerializer):
    message_count = serializers.SerializerMethodField()
    selected_documents = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = [
            "id", "title", "scope", "document_ids", "selected_documents", "message_count",
            "created_at", "updated_at",
        ]

    def get_message_count(self, obj) -> int:
        return obj.messages.count()

    def get_selected_documents(self, obj) -> list[dict]:
        if not obj.document_ids:
            return []
        documents = {
            str(document.id): document
            for document in owned_documents(obj.owner_id).filter(id__in=obj.document_ids)
        }
        return [
            {
                "id": str(document.id),
                "display_title": document.display_title,
                "original_filename": document.original_filename,
            }
            for document_id in obj.document_ids
            if (document := documents.get(str(document_id))) is not None
        ]

    def validate(self, attrs):
        scope = attrs.get("scope", getattr(self.instance, "scope", "all"))
        document_ids = attrs.get(
            "document_ids", list(getattr(self.instance, "document_ids", []))
        )

        if scope not in {"all", "selected"}:
            raise serializers.ValidationError({"scope": "must be 'all' or 'selected'"})

        if scope == "all":
            attrs["document_ids"] = []
            return attrs

        # Preserve the user's ordering while removing accidental duplicates.
        document_ids = list(dict.fromkeys(document_ids))
        if not document_ids:
            raise serializers.ValidationError(
                {"document_ids": "select at least one available document"}
            )

        # Another user's document is reported as "unavailable", exactly like a
        # missing one, so scoping cannot be used to probe for other users' ids.
        available_ids = set(
            owned_documents(self.context["request"].user).filter(
                id__in=document_ids, status__in=CHAT_READY_STATUSES
            ).values_list("id", flat=True)
        )
        missing = [str(document_id) for document_id in document_ids if document_id not in available_ids]
        if missing:
            raise serializers.ValidationError(
                {"document_ids": f"unavailable document(s): {', '.join(missing)}"}
            )

        attrs["document_ids"] = document_ids
        return attrs


class ConversationDetailSerializer(ConversationSerializer):
    messages = MessageSerializer(many=True, read_only=True)

    class Meta(ConversationSerializer.Meta):
        fields = ConversationSerializer.Meta.fields + ["messages"]


class ConversationViewSet(viewsets.ModelViewSet):
    queryset = Conversation.objects.prefetch_related("messages__attachments").all()

    def get_queryset(self):
        return super().get_queryset().filter(owner=self.request.user)

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    def get_serializer_class(self):
        return (
            ConversationSerializer if self.action == "list" else ConversationDetailSerializer
        )

    @action(detail=True, methods=["get"])
    def messages(self, request, pk=None):
        conversation = self.get_object()
        return Response(MessageSerializer(conversation.messages.all(), many=True).data)
