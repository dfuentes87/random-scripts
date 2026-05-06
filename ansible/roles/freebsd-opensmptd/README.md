freebsd-opensmptd
=================

Configures the mail services from the Redis through ClamAV sections of the
FreeBSD OpenSMTPD mail-server notes. The role is intended to run against the
mail jail itself, not against the jail host.

Requirements
------------

The jail must be reachable by Ansible and able to install FreeBSD packages.
TLS certificates should already be available inside the jail at
`freebsd_opensmptd_cert_fullchain` and `freebsd_opensmptd_cert_privkey`, usually
by mounting the Let's Encrypt directory from the nginx/certbot jail or host.

Role Variables
--------------

Required deployment values:

```yaml
freebsd_opensmptd_domain: example.net
freebsd_opensmptd_hostname: mail.example.net
freebsd_opensmptd_redis_password: replace-with-a-random-redis-password
freebsd_opensmptd_rspamd_redis_servers:
  - 127.0.0.1
freebsd_opensmptd_clamav_rspamd_server: 127.0.0.1:3310
```

The role creates the `vmail` service account. Mailbox account creation is
intentionally separate from this service role. Use
`ansible/playbooks/freebsd-opensmptd-users.yml` for mailbox setup.

```yaml
freebsd_opensmptd_mail_users:
  - address: user@example.net
    password_hash: replace-with-smtpctl-encrypt-output

freebsd_opensmptd_virtuals:
  - address: user@example.net
    destination: vmail
  - address: postmaster@example.net
    destination: user@example.net
```

Set `freebsd_opensmptd_build_table_passwd: true` if
`opensmtpd-extras-table-passwd` is unavailable or broken and the jail should
build it from source.

When DKIM generation is enabled, the role writes DNS-ready TXT records in both
split and unsplit formats to `freebsd_opensmptd_dkim_dns_output_path` on the
Ansible controller. The private key remains on the jail at
`freebsd_opensmptd_dkim_key_path`.

Dependencies
------------

None.

Example Playbook
----------------

See `ansible/playbooks/freebsd-opensmptd.yml` for service setup and
`ansible/playbooks/freebsd-opensmptd-users.yml` for mailbox account creation.
For a new jail, run the service setup playbook first, then run the users
playbook whenever mailbox users or aliases change.

The playbooks use sample values only. Replace the domain, hostname, Redis
password, IP addresses, certificate paths, users, and virtual mappings before
running them against a real jail.
