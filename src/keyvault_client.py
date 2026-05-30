"""
keyvault_client.py

Módulo auxiliar para recuperar segredos e certificados do Azure Key Vault.

Usa DefaultAzureCredential para autenticação — funciona via:
  - az login (desenvolvimento local)
  - Managed Identity (produção em Azure)
  - Variáveis de ambiente AZURE_CLIENT_ID / AZURE_CLIENT_SECRET (CI/CD)
"""

import base64
import os
from functools import cached_property

from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
from azure.keyvault.certificates import CertificateClient
from azure.keyvault.secrets import SecretClient


class KeyVaultClient:
    """
    Acessa segredos e certificados no Azure Key Vault via DefaultAzureCredential.

    O Key Vault armazena automaticamente o PFX de cada certificado como um secret
    com o mesmo nome (base64-encoded). Isso nos permite recuperar a chave privada
    para autricação com CertificateCredential sem nenhum arquivo local.
    """

    def __init__(self, vault_url: str) -> None:
        self._vault_url = vault_url
        mi_client_id = os.environ.get("MI_CLIENT_ID")
        self._credential = (
            ManagedIdentityCredential(client_id=mi_client_id)
            if mi_client_id
            else DefaultAzureCredential()
        )

    @cached_property
    def _secret_client(self) -> SecretClient:
        return SecretClient(vault_url=self._vault_url, credential=self._credential)

    @cached_property
    def _cert_client(self) -> CertificateClient:
        return CertificateClient(vault_url=self._vault_url, credential=self._credential)

    def get_secret(self, name: str) -> str:
        """Retorna o valor de um secret armazenado no Key Vault."""
        return self._secret_client.get_secret(name).value

    def get_sp_cert_bytes(self, cert_name: str) -> bytes:
        """
        Retorna o certificado do Service Principal como bytes PFX (PKCS#12).

        O Azure Key Vault armazena o PFX completo (incluindo chave privada)
        como um secret com o mesmo nome do certificado, codificado em base64.
        Esse secret é criado automaticamente quando o certificado é importado
        ou gerado pelo Key Vault.

        Esses bytes podem ser passados diretamente ao CertificateCredential
        do azure-identity via o parâmetro `certificate_data`.
        """
        secret = self._secret_client.get_secret(cert_name)
        return base64.b64decode(secret.value)


def build_kv_client() -> KeyVaultClient:
    """
    Constrói um KeyVaultClient lendo a URL do Key Vault da variável de ambiente
    AZURE_KEYVAULT_URL.
    """
    vault_url = os.environ.get("AZURE_KEYVAULT_URL")
    if not vault_url:
        raise EnvironmentError(
            "Variável de ambiente AZURE_KEYVAULT_URL não definida. "
            "Verifique o arquivo .env ou o ambiente de execução."
        )
    return KeyVaultClient(vault_url)
