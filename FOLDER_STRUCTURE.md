# Project Directory Structure

Overview and explanation of the folder and file structure for the **CrewLink** (`member-callout-exercise`) repository.

---

## High-Level Tree

```text
member-callout-exercise/
├── README.md                     # High-level overview of the CrewLink platform
├── DESIGN.md                     # Specifications & architecture design documents
├── django-member-callout/        # Backend application (Django / Python)
│   ├── manage.py
│   ├── .venv/
│   └── member_callout_exercise/  # Django project configuration & settings
│       ├── settings.py
│       ├── urls.py
│       ├── asgi.py
│       └── wsgi.py
└── flutter_member_callout/       # Frontend client application (Flutter / Dart)
    ├── lib/                      # Flutter app source code
    │   └── main.dart
    ├── pubspec.yaml              # Dependencies and configuration
    ├── android/                  # Android platform shell
    ├── web/                      # Web platform shell
    └── test/                     # Unit / widget test suite
```

---

## Detailed Component Breakdown

### 1. `django-member-callout/`
The backend service powering the API and business logic using Django (Python):
* **`manage.py`**: Command-line entry point for Django management commands (e.g. running servers, migrations, creating apps).
* **`member_callout_exercise/`**: The primary Django project configuration package:
  * **`settings.py`**: Global application settings, installed apps, middleware, database connection, and security configurations.
  * **`urls.py`**: Root URL routing and API route declarations.
  * **`wsgi.py` / `asgi.py`**: Web server gateway interfaces for deployment.
* **`.venv/`**: Python virtual environment containing installed packages.

---

### 2. `flutter_member_callout/`
The cross-platform client application built with Flutter (Dart), targeting Web and Mobile (Android):
* **`lib/`**: Contains core Dart application source code:
  * **`main.dart`**: Main entry point (`runApp()`) and root widget tree.
* **`pubspec.yaml`**: Package manifest defining Dart/Flutter dependencies, assets, and project metadata.
* **`pubspec.lock`**: Exact dependency versions locked for reproducible builds.
* **`web/` & `android/`**: Platform-specific container projects for running and packaging the app on web browsers and Android devices.
* **`test/`**: Unit and widget test suites.

---

### 3. Root Files
* **`README.md`**: Project introduction and high-level description of the CrewLink platform.
* **`DESIGN.md`**: System specifications, problem statement for Local 27 callouts, client requirements (handling offline members, broadcast delivery guarantees, response statuses, and privacy controls across union locals), as well as architectural and data model designs.
* **`.gitignore`**: Git rules ignoring build artifacts, caches, OS metadata, and local database files.
