from rest_framework import viewsets, permissions
from .models import Listing, Booking
from .serializers import ListingSerializer, BookingSerializer
import os
import uuid
import requests
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.core.mail import send_mail
from .models import Payment, Booking
from django.conf import settings

class ListingViewSet(viewsets.ModelViewSet):
    """
    API endpoint that allows listings to be viewed or edited.
    """
    queryset = Listing.objects.all()
    serializer_class = ListingSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]


class BookingViewSet(viewsets.ModelViewSet):
    """
    API endpoint that allows bookings to be viewed or edited.
    """
    queryset = Booking.objects.all()
    serializer_class = BookingSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]


CHAPA_BASE_URL = "https://api.chapa.co/v1"
CHAPA_SECRET_KEY = os.getenv('CHAPA_SECRET_KEY')


@api_view(['POST'])
def initiate_payment(request):
    """Initiate payment through Chapa API."""
    try:
        booking_id = request.data.get('booking_id')
        booking = get_object_or_404(Booking, id=booking_id)
        amount = booking.total_price
        email = booking.user.email

        # Generate unique transaction reference
        tx_ref = f"tx-{uuid.uuid4()}"

        headers = {
            "Authorization": f"Bearer {CHAPA_SECRET_KEY}",
            "Content-Type": "application/json",
        }

        payload = {
            "amount": str(amount),
            "currency": "ETB",
            "email": email,
            "first_name": booking.user.first_name,
            "last_name": booking.user.last_name,
            "tx_ref": tx_ref,
            "callback_url": request.build_absolute_uri(f"/api/payments/verify/?tx_ref={tx_ref}"),
            "return_url": "https://yourfrontend.com/payment/success",
            "customization[title]": "Booking Payment",
            "customization[description]": f"Payment for booking {booking.id}",
        }

        response = requests.post(f"{CHAPA_BASE_URL}/transaction/initialize", json=payload, headers=headers)
        response_data = response.json()

        if response.status_code == 200 and response_data.get("status") == "success":
            # Save payment record
            Payment.objects.create(
                booking=booking,
                amount=amount,
                transaction_id=tx_ref,
                status="Pending",
            )
            return Response({
                "message": "Payment initiated successfully.",
                "checkout_url": response_data["data"]["checkout_url"],
                "transaction_id": tx_ref
            }, status=status.HTTP_200_OK)
        else:
            return Response({
                "error": "Failed to initialize payment.",
                "details": response_data
            }, status=status.HTTP_400_BAD_REQUEST)

    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
def verify_payment(request):
    """Verify Chapa payment status."""
    tx_ref = request.GET.get('tx_ref')
    if not tx_ref:
        return Response({"error": "Missing transaction reference."}, status=status.HTTP_400_BAD_REQUEST)

    headers = {
        "Authorization": f"Bearer {CHAPA_SECRET_KEY}",
    }

    response = requests.get(f"{CHAPA_BASE_URL}/transaction/verify/{tx_ref}", headers=headers)
    response_data = response.json()

    payment = get_object_or_404(Payment, transaction_id=tx_ref)
    booking = payment.booking

    if response.status_code == 200 and response_data.get("status") == "success":
        chapa_status = response_data["data"]["status"]

        if chapa_status == "success":
            payment.status = "Completed"
            payment.save()

            # Send confirmation email (Celery optional)
            send_mail(
                subject="Payment Successful",
                message=f"Your payment for booking {booking.id} was successful.",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[booking.user.email],
            )

            return Response({"message": "Payment verified successfully."}, status=status.HTTP_200_OK)

        else:
            payment.status = "Failed"
            payment.save()
            return Response({"message": "Payment failed."}, status=status.HTTP_400_BAD_REQUEST)

    return Response({"error": "Verification failed.", "details": response_data}, status=status.HTTP_400_BAD_REQUEST)
