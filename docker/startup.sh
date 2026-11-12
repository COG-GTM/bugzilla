#!/bin/bash

[ -z "$BZ_DB_HOST" ] && echo "Missing Docker Environment, check docker-compose.yml" && exit -1
cd /var/www/html

# Strangler proxy: when BZ_PYTHON_UPSTREAM is set (e.g. http://bugzilla5.python:8000), the REST
# routes already ported to Python are reverse-proxied there; everything else stays on Perl.
# One file per migrated module in docker/python-proxy-routes.d/<module>.txt (one path regex per line).
# Perl is also exposed directly (no proxy rules) on port 8081 so the Python service can forward the
# REST resources it has not ported yet without looping back through this proxy.
mkdir -p /etc/apache2/bugzilla-proxy
rm -f /etc/apache2/bugzilla-proxy/*.conf
if [ -n "$BZ_PYTHON_UPSTREAM" ]; then
  {
    echo "ProxyPreserveHost On"
    echo "ProxyRequests Off"
    cat /root/docker/python-proxy-routes.d/*.txt | grep -vE '^\s*(#|$)' | while read -r pattern; do
      echo "ProxyPassMatch \"^/rest\\.cgi/(${pattern})(/.*)?\$\" \"${BZ_PYTHON_UPSTREAM%/}/rest.cgi/\$1\$2\""
    done
  } > /etc/apache2/bugzilla-proxy/python.conf
  echo "Python strangler proxy enabled -> $BZ_PYTHON_UPSTREAM"
  cat /etc/apache2/bugzilla-proxy/python.conf
fi
cat > /etc/apache2/sites-enabled/001-perl-direct.conf <<'APACHE'
# Perl-only listener (no strangler ProxyPass rules); used as BZ_PERL_UPSTREAM by the Python service.
Listen 8081
<VirtualHost *:8081>
	DocumentRoot /var/www/html
	ErrorLog ${APACHE_LOG_DIR}/error.log
	CustomLog ${APACHE_LOG_DIR}/access.log combined
</VirtualHost>
APACHE
apachectl start
while :
do
  echo "Waiting for database to be available..."
  nc -z $BZ_DB_HOST $BZ_DB_PORT
  [ $? -eq 0 ] && break
  sleep 2
done
echo "Checking database..."
cat - >/root/docker/myclient-root.cnf <<EOF
[client]
host='$BZ_DB_HOST'
port=$BZ_DB_PORT
user='root'
password='$MARIADB_ROOT_PASSWORD'
EOF
TESTSQL="show databases like '$BZ_DB_NAME'"
DBEXISTS=`echo "$TESTSQL" | mysql --defaults-file=/root/docker/myclient-root.cnf -BN`
[ -z "$DBEXISTS" ] && (
  echo "Database not found, creating..."
  CREATESQL="
CREATE DATABASE \`$BZ_DB_NAME\`;
GRANT SELECT, INSERT,
UPDATE, DELETE, INDEX, ALTER, CREATE, LOCK TABLES,
CREATE TEMPORARY TABLES, DROP, REFERENCES ON \`$BZ_DB_NAME\`.*
TO '$BZ_DB_USER'@'%' IDENTIFIED BY '$BZ_DB_PASS';
FLUSH PRIVILEGES;
"
  echo "$CREATESQL" | mysql --defaults-file=/root/docker/myclient-root.cnf -BN
)
echo "Beginning checksetup..."
perl -pi -e "
s/%%BZ_ADMIN_EMAIL%%/'${BZ_ADMIN_EMAIL//@/\\@}'/;
s/%%BZ_ADMIN_PASSWORD%%/'${BZ_ADMIN_PASSWORD//@/\\@//$/\\$}'/;
s/%%BZ_ADMIN_REALNAME%%/'${BZ_ADMIN_REALNAME//@/\\@//$/\\$}'/;
s/%%BZ_DB_HOST%%/'$BZ_DB_HOST'/;
s/%%BZ_DB_PORT%%/$BZ_DB_PORT/;
s/%%BZ_DB_NAME%%/'$BZ_DB_NAME'/;
s/%%BZ_DB_USER%%/'$BZ_DB_USER'/;
s/%%BZ_DB_PASS%%/'${BZ_DB_PASS//@/\\@//$/\\$}'/;
s@%%BZ_URLBASE%%@'${BZ_URLBASE//@/\\@}'@;
" /root/docker/checksetup_answers.txt
perl checksetup.pl /root/docker/checksetup_answers.txt
echo "Checksetup completed."

LOGIN_USER="Admin user: $BZ_ADMIN_EMAIL"
LOGIN_PASS="Admin password: $BZ_ADMIN_PASSWORD"
cat - <<EOF
#########################################
##                                     ##
##  Your Bugzilla installation should  ##
##         now be reachable at:        ##
##                                     ##
EOF
printf "##%*s%*s##\n" $(( (${#BZ_URLBASE} + 37) / 2)) $BZ_URLBASE $(( 37 - ((${#BZ_URLBASE} + 37) / 2)  )) " "
cat - <<EOF
##                                     ##
EOF
printf "##%*s%*s##\n" $(( (${#LOGIN_USER} + 37) / 2)) "$LOGIN_USER" $(( 37 - ((${#LOGIN_USER} + 37) / 2)  )) " "
printf "##%*s%*s##\n" $(( (${#LOGIN_PASS} + 37) / 2)) "$LOGIN_PASS" $(( 37 - ((${#LOGIN_PASS} + 37) / 2)  )) " "
cat - <<EOF
##                                     ##
##   user/password only valid if you   ##
##    haven't already changed them.    ##
##                                     ##
#########################################
EOF
# don't exit docker
while [ 1 ]; do sleep 1000; done
