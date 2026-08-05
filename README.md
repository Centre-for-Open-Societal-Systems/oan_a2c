# OpenAgriNet - Access to Credit (A2C) Marketplace

Backend implementation for the Access to Credit (A2C) Application, providing a centralized marketplace for farmers to access loan products and manage consent-driven credit information sharing.

## Overview

This Frappe application acts as the backend for the A2C marketplace. It includes:
* **Loan Product Management**: Banks can define and manage loan products (interest rates, limits, statuses).
* **Lead Generation & Assignment**: Processing applications, capturing credit data, and assigning them to bank agents.
* **Consent Engine**: Interfaces with OpenG2P for managing farmer consent for personal data sharing.
* **Notification System**: Robust alert mechanisms for bank administrators and agents.
* **API Endpoints**: Full API suite for frontend interaction, including webhooks and JWT-based authentication.

## Installation

To install this application on your Frappe bench, run the following commands:

```bash
# Get the application
bench get-app https://github.com/faizmagic/oan_a2c.git

# Install it on your site (replace mysite.localhost with your site name)
bench --site mysite.localhost install-app oan_a2c
```

## Configuration

This application requires specific keys to be configured in your site's `site_config.json`. You can set these using the `bench set-config` command.

### JWT & Encryption Settings
Used for securing authentication and session tokens:
```bash
bench --site mysite.localhost set-config encryption_key "your-encryption-key-here"
bench --site mysite.localhost set-config secret_key "your-secret-key-here"
```

### OpenG2P Integration
Used by the consent engine to interface with the OpenG2P service for data sharing and verification:
```bash
bench --site mysite.localhost set-config openg2p_base_url "https://api.openg2p.example.com"
bench --site mysite.localhost set-config openg2p_db "openg2p_database_name"
bench --site mysite.localhost set-config openg2p_username "api_username"
bench --site mysite.localhost set-config openg2p_password "api_password"
```

*Note: In Dockerized environments (like `frappe_docker`), you may alternatively pass these via environment variables if your container startup scripts template the site configs.*

## Running Tests

To run the automated test suite for this application:

```bash
bench --site mysite.localhost run-tests --app oan_a2c
```

## License

Please see `license.txt` for license information.
