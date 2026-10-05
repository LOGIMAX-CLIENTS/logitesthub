---
login: none
tags: [login, negative]
---

# Login is refused with a wrong password

## Open the login page
Navigate to {{base_url}}index.php/admin/dashboard.
Assert the page title contains 'Login'.

## Sign in with a wrong password
Type {{username}} into the User Name field.
Type WrongPass@123 into the Password field.
Select the first available option in the Select Branch dropdown (#branch_select).
Click the Sign In button.

## Stays on the login page with an error
Assert the text 'Invalid Username or Password' is visible.
Assert the page title contains 'Login'.
