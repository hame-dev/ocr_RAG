from __future__ import annotations

from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from chat.models import Conversation, Message
from documents.models import Document


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = [
            "seq", "role", "content", "tool_calls", "citations", "citation_mode",
            "latency_ms", "model_id", "is_partial", "error", "created_at",
        ]


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
            for document in Document.objects.filter(id__in=obj.document_ids)
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

        available_ids = set(
            Document.objects.filter(
                id__in=document_ids, status__in=["ready", "indexed"]
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
    queryset = Conversation.objects.prefetch_related("messages").all()

    def get_serializer_class(self):
        return (
            ConversationSerializer if self.action == "list" else ConversationDetailSerializer
        )

    @action(detail=True, methods=["get"])
    def messages(self, request, pk=None):
        conversation = self.get_object()
        return Response(MessageSerializer(conversation.messages.all(), many=True).data)
