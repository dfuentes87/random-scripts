freebsd-opensmptd
=================

Sets up OpenSMTPD, rspamd, Dovecot, Redis, and ClamAV. Redis is used for rspamd caching.
The role is designed to run against a dedicated mail jail, not against the host.

Requirements
------------

Jail must already be created, and TLS certificates should already be available inside 
the jail at `freebsd_opensmptd_cert_fullchain` and `freebsd_opensmptd_cert_privkey` paths,
usually by mounting the Let's Encrypt directory from the nginx/certbot jail or host.

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

Redis defaults to `127.0.0.1` because rspamd is expected to run in the same jail.

The role creates the `vmail` service account. Mailbox account creation is
intentionally separate from this service role. Use
`ansible/playbooks/freebsd-opensmptd-users.yml` for mailbox setup.

Mailbox users are automatically mapped to `vmail` in
`/usr/local/etc/mail/virtuals`; aliases are managed separately through
`freebsd_opensmptd_mail_aliases`.

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
