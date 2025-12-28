from django.contrib.auth.views import LoginView, LogoutView


class CustomLoginView(LoginView):
    """Custom login view with redirect to sources list"""
    template_name = 'registration/login.html'
    redirect_authenticated_user = True


class CustomLogoutView(LogoutView):
    """Custom logout view"""
    next_page = 'login'

