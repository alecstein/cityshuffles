from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect


@login_required
def home(request):
    return redirect("bookings:index")
