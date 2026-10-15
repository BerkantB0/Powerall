# Third-party notices

Powerall's MIT License applies to the project's own code. Dependencies keep their respective licenses.

The dashboard includes shadcn/ui components and shadcn Tailwind styles, licensed under MIT, copyright 2023 shadcn. The full notice is retained in [dashboard/vendor/shadcn-tailwind-4.13.0.LICENSE.md](dashboard/vendor/shadcn-tailwind-4.13.0.LICENSE.md).

Other dependencies include Next.js, React, Lucide, Tailwind CSS, Python cryptography, Psycopg and Gunicorn. Their source distributions contain their license notices. Docker images also contain third-party software; review the upstream image and package licenses when redistributing images.

The frontend Docker image includes installed npm license and notice files under `/app/licenses/npm`, plus the shadcn notice under `/app/licenses/shadcn-ui.txt`. Python package notices remain in the API image's installed package metadata. Both application images include Powerall's license and this file.
